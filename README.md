# Setting up a National Voter File Batch Pipeline

Create the AWS Batch
1. Create compute Environment
 . unmangaged
 . AWSBatchServiceRole


## Create EFS Shared File Store
`sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 fs-1567e35c.efs.us-east-1.amazonaws.com:/ efs`

## Create Launch configuration
1. Create from AWS Marketplace - search for amazon-ecs-optimized
2. M3 - Large
3. Role: ecsInstanceRole
4. User Data:
```#!/bin/bash
yum update -y
yum -y install nfs-utils
mkdir -p /home/ec2-user/efs
sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 fs-1567e35c.efs.us-east-1.amazonaws.com:/ /home/ec2-user/efs
echo ECS_CLUSTER=national-voter-file-compute-env_Batch_d256c62e-2383-35b4-84f2-6a9228064c84 >> /etc/ecs/ecs.config
service docker restart && start ecs
```

5. Don't forget to add NFS Traffic permission between the Node security group and NFS to the share


## Create S3Ops Job
1. Create S3Reader Role (for ECS Task)
2. Image: garland/aws-cli-docker:latest
3. 2 vCPUS, 800Mb
4. Volumes - name: work, Source-path /home/ec2-user/efs
5. Mount point - Container Path: /work, Source-volume: work

## Create ETL Job
1. Image: getmovement/national-voter-file
2. 2 vCPUs, 700Mb
4. Volumes - name: work, Source-path /home/ec2-user/efs
5. Mount point - Container Path: /work, Source-volume: work

Create a config file in /work on the EC2 instance:

`vi /home/ec2-user/efs/load_conf.json`

Include:
```
{
  "pdi_path": "/opt/pentaho/data-integration",
  "nvf_path": "/national-voter-file",
  "data_path": "/national-voter-file/data",
  "db_url": "jdbc:postgresql://nvf1.cyafxnvnxihq.us-east-1.rds.amazonaws.com:5432/voter",
  "db_username": "postgres",
  "db_password": "_____________"
}
```
