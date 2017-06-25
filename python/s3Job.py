from __future__ import print_function
import os
import string
import boto3
import json
import urllib
import re
from random import SystemRandom


reporter_dict = {
    "WA": "1",
    "OH": "2",
    "NY": "3",
    "FL": "4",
    "MI": "5"
}


def create_es_domain(client, host):
    """
    Create Elasticsearch service with random hostname (potentially configurable),
    and default config for now
    """
    response = client.create_elasticsearch_domain(
        DomainName=host,
        ElasticsearchVersion='5.3',
        ElasticsearchClusterConfig={
            'InstanceType': 't2.small.elasticsearch',
            'InstanceCount': 3,
            'DedicatedMasterEnabled': False,
            'ZoneAwarenessEnabled': False
        },
        EBSOptions={
            'EBSEnabled': True,
            'VolumeType': 'standard',
            'VolumeSize': 10
        },
        AccessPolicies='arn:aws:iam::{}:role/GeocoderRole'.format(os.getenv('AWS_ACCOUNT')),
        SnapshotOptions={
            'AutomatedSnapshotStartHour': 0
        }
    )


def submit_load_elasticsearch(client, host, bucket, key):
    """
    Run es_tiger_loader.py as normal, will need S3 bucket with information as var
    """
    # TODO: Where is geography string pulled from? Potentially in key?
    command = {'command': ['es_tiger_loader.py', 'PLACEHOLDER', '-h', host, '-b', bucket]}

    job_submit_result = client.submit_job(
        jobName='LoadElasticsearchTBD',
        jobQueue='National-Voter-File-Job-Queue',
        jobDefinition='Geocoder',
        containerOverrides=command
    )

    job_id = job_submit_result['jobId']
    return job_id


def submit_run_geocoder_job(client, bucket, host, key):
    """
    Run run.py to actually geocode results
    - Need to pass --s3_bucket parameter with the bucket where output will be stored
    - Can also include full S3 path to key because path will be split and put in
      data/ in processing
    """
    output_file = '.'.join(key.split('.')[:-1]) + '_output.' + key.split('.')[-1]
    command = {'command': ['run.py', key, '-o', output_file, '-s', 'PLACEHOLDER', '-b', bucket, '-h', host]}

    job_submit_result = client.submit_job(
        jobName='LoadElasticsearchTBD',
        jobQueue='National-Voter-File-Job-Queue',
        jobDefinition='Geocoder',
        containerOverrides=command
    )

    job_id = job_submit_result['jobId']
    return job_id


def delete_elasticsearch(client, bucket, host):
    """
    Can this be a short command queue to run at end? Does it have to be added to the container?
    """
    response = client.delete_elasticsearch_domain(DomainName=host)


def submit_file_copy_job(client, bucket, key):
    s3_path = "s3://%s/%s" % (bucket, key)
    command = {"command": ["sh", "-cxv", "aws s3 cp %s /work; chmod go+rw /work/%s" % (s3_path, key)]}

    job_submit_result = client.submit_job(jobName='CopyVoterFile', jobQueue='National-Voter-File-Job-Queue',
                                          jobDefinition='S3Ops', containerOverrides=command)

    job_id = job_submit_result['jobId']
    return job_id


def submit_unzip_job(client, input_file, dependsOn):
    command = {"command": ["sh", "-cxv", "gunzip -f "+input_file]}

    job_submit_result = client.submit_job(jobName='UnzipVoterFile', jobQueue='National-Voter-File-Job-Queue',dependsOn=dependsOn,
                                          jobDefinition='BusyBox', containerOverrides=command)

    job_id = job_submit_result['jobId']
    return job_id


def submit_transform_job(batch_client, input_file, state_name, dependsOn):
    xform_command = {"command": ["--configfile", "/work/load_conf.json", "-s", state_name, "--input_file",
                                 input_file, "transform"]}

    job_submit_result = batch_client.submit_job(jobName='Transform' + state_name,
                                                jobQueue='National-Voter-File-Job-Queue',
                                                jobDefinition='ETL', dependsOn=dependsOn,
                                                containerOverrides=xform_command)
    return job_submit_result['jobId']


def submit_precinct_job(batch_client, input_file, state_name, report_date, dependsOn):
    xform_command = {
        "command": ["--configfile", "/work/load_conf.json", "--update_jndi", "--report_date", report_date, "-s",
                    state_name, "--input_file",
                    input_file, "precincts"]}

    job_submit_result = batch_client.submit_job(jobName='LoadPrecints' + state_name + report_date,
                                                jobQueue='National-Voter-File-Job-Queue',
                                                jobDefinition='ETL', dependsOn=dependsOn,
                                                containerOverrides=xform_command)
    return job_submit_result['jobId']


def submit_load_job(batch_client, input_file, state_name, report_date, reporter, dependsOn):
    xform_command = {"command": ["--configfile", "/work/load_conf.json", "--update_jndi", "--report_date", report_date,
                                 "--reporter_key", reporter, "-s", state_name, "--input_file",
                                 input_file, "load"]}

    job_submit_result = batch_client.submit_job(jobName='LoadVoterFile' + state_name + report_date,
                                                jobQueue='National-Voter-File-Job-Queue',
                                                jobDefinition='ETL', dependsOn=dependsOn,
                                                containerOverrides=xform_command)
    return job_submit_result['jobId']


def run_geocoder_tasks(batch_client, es_client):
    rand_str = ''.join(SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(6))
    es_host = 'geocoder-{}'.format(rand_str)
    create_es_domain(es_host)


def lambda_handler(event, context):
    batch_client = boto3.client('batch')
    es_client = boto3.client('es')
    """:type: pyboto3.batch"""

    s3 = boto3.resource('s3')

    # Extract the bucket name and object name
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'])

    # Determine the state associated with this bucket
    bucket_tagging = s3.BucketTagging(bucket)
    state_tags = [el for el in bucket_tagging.tag_set if el['Key'] == 'state_name']
    state_name = state_tags[0]['Value']

    reporter = reporter_dict[state_name]

    # Extract the file date
    m = re.search("_([0-9]{4})([0-9]{2})([0-9]{2}).*", key)
    if not m:
        raise Exception("Can't determine file date from " + key)

    report_date = "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))

    print("Processing file for " + state_name + " on " + report_date)

    # Copy the file from S3 to our local EFS mount
    cp_job = submit_file_copy_job(batch_client, bucket, key)
    print("cp job is " + cp_job)

    input_file = "/work/" + key

    # Unzip the file once it is copied (if neccessary)
    if input_file.endswith('gz'):
        file_ready_job = submit_unzip_job(batch_client, input_file, [{'jobId': cp_job}])
        m = re.match("(.*)\\.gz$", input_file)
        input_file = m.group(1)
    else:
        file_ready_job = cp_job

    # Schedule a transform job after that
    transform_job = submit_transform_job(batch_client, input_file, state_name, [{'jobId': file_ready_job}])

    # The precinct job can run in parallel
    precinct_job = submit_precinct_job(batch_client, input_file, state_name, report_date, [{'jobId': file_ready_job}])

    # The load job needs the transform and the precincts
    load_job = submit_load_job(batch_client, "/work/" + state_name.lower() + "_output.csv", state_name, report_date,
                               reporter, [{'jobId': transform_job}, {'jobId': precinct_job}])
