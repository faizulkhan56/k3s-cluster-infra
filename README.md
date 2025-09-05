# Automate a K3s Cluster on AWS with Pulumi (Python) — Step‑by‑Step

This README is a **full, copy‑paste walkthrough** to provision a lightweight Kubernetes cluster (**K3s**) on **AWS EC2** using **Pulumi (Python)**, and then deploy a demo **Nginx** app exposed via **Ingress** (served by K3s’ built‑in Traefik). It includes both the **Pulumi setup** and the **Kubernetes steps**, plus troubleshooting notes.

> Works great for learning, labs, and PoCs. For production, see the hardening notes.

---

## What you’ll build

- **AWS Infra (via Pulumi):**
  - VPC `10.0.0.0/16`
  - Public Subnet `10.0.1.0/24` (with automatic public IPs)
  - Internet Gateway + Route Table (0.0.0.0/0)
  - Security Group for K3s (22, 80/443 world; 6443/tcp & 8472/udp within VPC)
  - 1× **master** (`t3.small`) + N× **workers** (`t3.small`) — Ubuntu 24.04
  - **User data** to install K3s: master boots control plane, workers auto‑join using the master’s **private IP** + a **shared token**

- **Kubernetes (on the cluster):**
  - Nginx **Deployment**, **Service** (ClusterIP: 3000→80), and **Ingress** (Traefik)
  - Access the app at `http://<any-node-public-ip>/` (port 80)

---

## Prerequisites

- AWS account with programmatic access (Access key & Secret key)
- **AWS CLI v2** installed and configured (`aws configure`)
- **Pulumi CLI** installed  
  ```bash
  curl -fsSL https://get.pulumi.com | sh
  # restart your shell if needed, verify:
  pulumi version
  ```
- **Python 3.10+** and `pip`
- An **EC2 key pair name** (e.g., `k3s-cluster`) and the **private key file** on your local machine

> **Region used below:** `ap-southeast-1` (Singapore). Change as needed.

---

## 0) Prepare a working folder & virtualenv

```bash
mkdir -p ~/code/k3s-aws-pulumi
cd ~/code/k3s-aws-pulumi

python3 -m venv venv
source venv/bin/activate
```

Create `requirements.txt`:

```txt
pulumi>=3.120.0
pulumi-aws>=6.58.0
```

Install deps:

```bash
pip install -r requirements.txt
```

---

## 1) Login to Pulumi

```bash
pulumi login
```
- Use browser or access token to authenticate.
- You’ll see a “Welcome to Pulumi!” message on success.

---

## 2) Initialize a new Pulumi project

```bash
pulumi new aws-python
```
- Answer prompts (defaults are fine).
- **Set AWS region** when asked: `ap-southeast-1`
- A fresh project scaffold is created (with `__main__.py`, `Pulumi.yaml`, etc.).

> If you already have a project, you can skip this and keep your existing files.

---

## 3) Create (or confirm) an EC2 key pair

Create a key pair named `k3s-cluster` and save **private key** locally:

```bash
cd ~/.ssh/
aws ec2 create-key-pair --key-name k3s-cluster   --output text --query 'KeyMaterial' > k3s-cluster.id_rsa

chmod 400 k3s-cluster.id_rsa
ls -l ~/.ssh/k3s-cluster.id_rsa
```

> This makes AWS store the public key; you keep the private key locally.

Return to your project folder:

```bash
cd ~/code/k3s-aws-pulumi
```

---

## 4) Use config‑driven values in your Pulumi program

In your `__main__.py`, read values from **Pulumi stack config** with safe defaults:

```python
import os
import pulumi
import pulumi_aws as aws

# --- Configuration (from Pulumi stack) ---
config = pulumi.Config()
vpc_cidr = config.get("vpc_cidr") or "10.0.0.0/16"
public_subnet_cidr = config.get("public_subnet_cidr") or "10.0.1.0/24"
availability_zone = config.get("availability_zone") or "ap-southeast-1a"
ubuntu_ami_id = config.get("ami_id") or "ami-060e277c0d4cce553"  # Ubuntu 24.04
k3s_token = config.get("k3s_token") or "super-secret-token"       # Use --secret in real use
ssh_key_name = config.get("k3sCluster") or "k3s-cluster"          # EC2 key pair name

# (networking, SG, instances, user_data, exports ... continue below)
```

**Tip (preferred):** Instead of hard-coding `ami_id`, you can lookup the latest Ubuntu 24.04 AMI dynamically:
```python
ubuntu = aws.ec2.get_ami(
    most_recent=True,
    owners=["099720109477"],  # Canonical
    filters=[
        {"name": "name", "values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
        {"name": "virtualization-type", "values": ["hvm"]},
    ],
)
ubuntu_ami_id = ubuntu.id
```

---

## 5) Create/select a stack & set config values

Create a stack (e.g., `dev`) if you don’t have one:

```bash
pulumi stack init dev
# or select an existing one
pulumi stack select dev
```

Set config values (these map to the code above):

```bash
pulumi config set vpc_cidr 10.0.0.0/16
pulumi config set public_subnet_cidr 10.0.1.0/24
pulumi config set availability_zone ap-southeast-1a
pulumi config set ami_id ami-060e277c0d4cce553
pulumi config set k3sCluster k3s-cluster
pulumi config set k3s_token my-super-secret-token --secret
```

Verify:

```bash
pulumi config
```

Expected:
```
KEY                VALUE
vpc_cidr           10.0.0.0/16
public_subnet_cidr 10.0.1.0/24
availability_zone  ap-southeast-1a
ami_id             ami-060e277c0d4cce553
k3sCluster         k3s-cluster
k3s_token          [secret]
```


---

## 6) Deploy the infrastructure

```bash
pulumi up --yes
```

Pulumi will show a plan, then create:
- VPC, Subnet, IGW, Route Table + association
- Security Group for K3s
- 1× master, N× workers (Ubuntu 24.04)
- User data will install **K3s server** on master and **agents** on workers

If your program includes the helper to write SSH config entries, it will write **local** aliases like:

```
Host master
  HostName <master-public-ip>
  User ubuntu
  IdentityFile ~/.ssh/k3s-cluster.id_rsa

Host worker-1
  HostName <worker1-public-ip>
  User ubuntu
  IdentityFile ~/.ssh/k3s-cluster.id_rsa
```

> This file is on **your local machine** (`~/.ssh/config`), not inside the VMs.

---

## 7) SSH in & verify K3s

SSH using the alias (or use `ubuntu@<public-ip>` + `-i ~/.ssh/k3s-cluster.id_rsa`):

```bash
ssh master
```

On the master:
```bash
# K3s bundles kubectl at /usr/local/bin/kubectl and a kubeconfig at /etc/rancher/k3s/k3s.yaml
sudo kubectl get nodes -o wide
```

You should see the master + workers **Ready**.

> If kubeconfig permissions were adjusted in your user-data, you can also copy it:
> ```bash
> sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
> sudo chown $(id -u):$(id -g) ~/.kube/config
> kubectl get nodes
> ```

---

## 8) Deploy a sample Nginx app (Ingress via Traefik)

Create a manifest locally (or on the master) named `k3s-app.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: k3s-app
  labels: { app: k3s-app }
spec:
  replicas: 2
  selector:
    matchLabels: { app: k3s-app }
  template:
    metadata:
      labels: { app: k3s-app }
    spec:
      containers:
        - name: k3s-app
          image: nginx:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: k3s-app-service
  labels: { app: k3s-app }
spec:
  type: ClusterIP
  selector:
    app: k3s-app
  ports:
    - name: http
      port: 3000
      targetPort: 80
      protocol: TCP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: k3s-app-ingress
  annotations:
    ingress.kubernetes.io/ssl-redirect: "false"
spec:
  ingressClassName: traefik
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: k3s-app-service
                port:
                  number: 3000
```

Apply it:

```bash
kubectl apply -f k3s-app.yaml
kubectl get deploy,svc,ingress
```

Find a node **public IP** (or use the master’s public IP) and test:

```bash
curl http://<any-node-public-ip>/
# or open http://<any-node-public-ip>/ in your browser
```

> **Why Traefik?** K3s **includes Traefik** by default (unless disabled). It listens on host ports **80/443** on every node via a lightweight service (`klipper-lb`).

---

## 9) Clean up

```bash
pulumi destroy --yes
```

This will delete all AWS resources created by the stack.


---

## Troubleshooting & FAQs

### Workers didn’t join the cluster
- Ensure security group allows within‑VPC traffic:
  - **TCP 6443** (K3s API) from **VPC CIDR** to master
  - **UDP 8472** (Flannel VXLAN) within **VPC CIDR** (node↔node)
- Confirm workers used `K3S_URL=https://<master-private-ip>:6443` and **same token**.

### I didn’t install kubectl — how is it available?
- K3s **bundles** `kubectl` (and `crictl`, `ctr`). The installer drops `/usr/local/bin/kubectl` and a kubeconfig at `/etc/rancher/k3s/k3s.yaml`.

### What CRI is installed?
- K3s ships **containerd** as the container runtime by default.

### Why does `kubectl get ingress` show `CLASS=traefik`?
- K3s auto-installs Traefik v2 unless `--disable traefik` is set on the server.

### Where did the SSH config come from?
- Your Pulumi program can write **local** `~/.ssh/config` entries on the **machine running Pulumi**, so you can `ssh master` easily. It does **not** modify the VMs’ `.ssh` directories.

### Opened 443 only inside the subnet — can’t access HTTPS
- Open **80/443** from the internet (or from your IP) if you want external access:
  - Ingress:
    - TCP **80** from `0.0.0.0/0`
    - TCP **443** from `0.0.0.0/0`

### AMI mismatch / not found
- Prefer AMI **lookup** instead of hard-coding AMI IDs (varies by region). See the snippet above.

### Disable Traefik and use a different Ingress
- Install K3s server with:
  ```bash
  curl -sfL https://get.k3s.io | K3S_TOKEN="..." sh -s - server --cluster-init --disable traefik
  ```
- Then install another controller (e.g., NGINX Ingress) and set `ingressClassName` accordingly.

---

## Appendix A — Example Security Group (reference)

Open the right ports and keep SSH tight in real environments:

- **SSH (22/tcp)**: from your IP (avoid `0.0.0.0/0`)
- **HTTP (80/tcp)** + **HTTPS (443/tcp)**: from `0.0.0.0/0` for quick testing
- **K3s API (6443/tcp)**: from **VPC CIDR** only
- **Flannel VXLAN (8472/udp)**: from **VPC CIDR** only
- **All egress**

---

## Appendix B — Example Pulumi config file shape

After running `pulumi config set ...`, your `Pulumi.dev.yaml` will look like:

```yaml
config:
  aws:region: ap-southeast-1
  <your-project-name>:vpc_cidr: 10.0.0.0/16
  <your-project-name>:public_subnet_cidr: 10.0.1.0/24
  <your-project-name>:availability_zone: ap-southeast-1a
  <your-project-name>:ami_id: ami-060e277c0d4cce553
  <your-project-name>:k3sCluster: k3s-cluster
  <your-project-name>:k3s_token:
    secure: <encrypted-secret>
```

Pulumi **namespaces** your keys with the project name automatically.

---

## Appendix C — Minimal Worker User‑Data (explained)

Each worker uses the master’s **private IP** to join (provided by Pulumi Outputs at deploy time):

```bash
#!/bin/bash
set -euxo pipefail
apt-get update -y
apt-get install -y curl
hostnamectl set-hostname k3s-worker${INDEX}
curl -sfL https://get.k3s.io | \
  K3S_TOKEN="${K3S_TOKEN}" \
  K3S_URL=https://<master-private-ip>:6443 \
  sh -s -
```

Pulumi templates this with the right values for each worker.

---

## Hardening (for later)

- Put workers in **private subnets**, access via **bastion** or **SSM Session Manager**.
- Use **ALB + external-dns + cert-manager** for real ingress/TLS.
- Store **K3s token** as a Pulumi **secret** (already shown).
- Lock down SSH to your IP; prefer SSM for auditability.
- Split stacks (e.g., `network`, `cluster`, `apps`) for larger projects.

---

Happy shipping! 🚀
