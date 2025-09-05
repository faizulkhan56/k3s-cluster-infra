import os
import pulumi
import pulumi_aws as aws

# Configuration
vpc_cidr = "10.0.0.0/16"
public_subnet_cidr = "10.0.1.0/24"
availability_zone = "ap-southeast-1a"
ubuntu_ami_id = "ami-060e277c0d4cce553"  # Ubuntu 24.04 LTS
k3s_token = "super-secret-token"
access_key = "k3s-cluster"


# Create a VPC
vpc = aws.ec2.Vpc("my-vpc",
    cidr_block=vpc_cidr,
    tags={
        "Name": "my-vpc"
    }
)

# Create a public subnet
public_subnet = aws.ec2.Subnet("public-subnet",
    vpc_id=vpc.id,
    cidr_block=public_subnet_cidr,
    availability_zone=availability_zone,
    map_public_ip_on_launch=True,
    tags={
        "Name": "public-subnet"
    }
)

# Create an Internet Gateway
igw = aws.ec2.InternetGateway("internet-gateway",
    vpc_id=vpc.id,
    tags={
        "Name": "igw"
    }
)

# Create a route table
public_route_table = aws.ec2.RouteTable("public-route-table",
    vpc_id=vpc.id,
    tags={
        "Name": "rt-public"
    }
)

# Create a route in the route table for the Internet Gateway
route = aws.ec2.Route("igw-route",
    route_table_id=public_route_table.id,
    destination_cidr_block="0.0.0.0/0",
    gateway_id=igw.id
)

# Associate the route table with the public subnet
route_table_association = aws.ec2.RouteTableAssociation("public-route-table-association",
    subnet_id=public_subnet.id,
    route_table_id=public_route_table.id
)

# Security Group for K3s Cluster Nodes
k3s_security_group = aws.ec2.SecurityGroup("k3s-secgrp",
    vpc_id=vpc.id,
    description="Security group for K3s cluster nodes",
    ingress=[
        # K3s API server
        {
            "protocol": "tcp",
            "from_port": 6443,
            "to_port": 6443,
            "cidr_blocks": [public_subnet.cidr_block]
        },
        # Flannel VXLAN
        {
            "protocol": "udp",
            "from_port": 8472,
            "to_port": 8472,
            "cidr_blocks": [public_subnet.cidr_block]
        },
        # SSH for administration
        {
            "protocol": "tcp",
            "from_port": 22,
            "to_port": 22,
            "cidr_blocks": ["0.0.0.0/0"]
        },
        # HTTP/HTTPS for applications
        {
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"]
        },
        {
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            "cidr_blocks": [public_subnet.cidr_block]
        },
        # Allow all internal cluster communication
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": [public_subnet.cidr_block]
        }
    ],
    egress=[
        # Allow all outbound traffic
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"]
        }
    ],
    tags={
        "Name": "k3s-cluster-sg"
    }
)

# Create K3s master instance
master_instance = aws.ec2.Instance("master-instance",
    instance_type="t3.small",
    vpc_security_group_ids=[k3s_security_group.id],
    ami=ubuntu_ami_id,
    subnet_id=public_subnet.id,
    key_name=access_key,
    associate_public_ip_address=True,
    user_data=f"""#!/bin/bash
    sudo apt update
    sudo hostnamectl set-hostname k3s-master
    # Install K3s server
    curl -sfL https://get.k3s.io | K3S_TOKEN="{k3s_token}" sh -s - server \\
    --cluster-init

    # Wait for K3s to be ready
    export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
    until /usr/local/bin/kubectl get nodes 2>/dev/null; do
        echo "Waiting for K3s to start..."
        sleep 5
    done

    chown ubuntu:ubuntu /etc/rancher/k3s/k3s.yaml
    chmod 600 /etc/rancher/k3s/k3s.yaml
    """,
    tags={
        "Name": "k3s-master"
    }
)

# Number of workers you want
num_workers = 2

# User data template (filled per worker)
def make_worker_userdata(ip, index):
    return f"""#!/bin/bash
    sudo apt update
    sudo hostnamectl set-hostname k3s-worker{index}
    # Install K3s agent
    curl -sfL https://get.k3s.io | K3S_TOKEN="{k3s_token}" K3S_URL=https://{ip}:6443 sh -s -
    """

# Create multiple worker instances
workers = []
for i in range(1, num_workers + 1):
    worker_user_data = master_instance.private_ip.apply(
        lambda ip, idx=i: make_worker_userdata(ip, idx)
    )

    worker = aws.ec2.Instance(f"worker{i}-instance",
        instance_type="t3.small",
        vpc_security_group_ids=[k3s_security_group.id],
        ami=ubuntu_ami_id,
        subnet_id=public_subnet.id,
        key_name=access_key,
        associate_public_ip_address=True,
        user_data=worker_user_data,
        tags={
            "Name": f"k3s-worker{i}"
        },
        opts=pulumi.ResourceOptions(
            depends_on=[master_instance]
        )
    )

    workers.append(worker)

# Export outputs
pulumi.export("vpc_id", vpc.id)
pulumi.export("public_subnet_id", public_subnet.id)
pulumi.export("igw_id", igw.id)
pulumi.export("public_route_table_id", public_route_table.id)

pulumi.export("master_instance_id", master_instance.id)
pulumi.export("master_instance_public_ip", master_instance.public_ip)
pulumi.export("master_private_ip", master_instance.private_ip)


for i, worker in enumerate(workers, start=1):
    pulumi.export(f"worker{i}_instance_id", worker.id)
    pulumi.export(f"worker{i}_instance_public_ip", worker.public_ip)
    pulumi.export(f"worker{i}_private_ip", worker.private_ip)


# Create config file
def create_config_file(all_ips):
    hosts = ["master"] + [f"worker-{i+1}" for i in range(len(workers))]
    config_lines = []

    for idx, host in enumerate(hosts):
        config_lines.append(f"Host {host}")
        config_lines.append(f"  HostName {all_ips[idx]}")
        config_lines.append(f"  User ubuntu")
        config_lines.append(f"  IdentityFile ~/.ssh/k3s-cluster.id_rsa\n")
        config_lines.append("")  # Blank line between hosts

    config_content = "\n".join(config_lines)
    
    config_path = os.path.expanduser("~/.ssh/config") # For ubuntu file system
    with open(config_path, "w") as config_file:
        config_file.write(config_content)

# Collect the IPs for all nodes
all_ips = [master_instance.public_ip] + [worker.public_ip for i, worker in enumerate(workers)]

# Create the config file with the IPs once the instances are ready
pulumi.Output.all(*all_ips).apply(create_config_file)
