# EC2 Deployment and CI/CD Guide

This project now includes CI/CD automation that deploys to EC2 whenever code is pushed to the main branch.

## 1. Important Security Action

Your local environment file currently contains real cloud and API credentials.
Rotate these credentials before production rollout:

- AWS_ACCESS_KEY and AWS_SECRET_ACCESS_KEY
- OPENAI_API_KEY

After rotation, keep real values only in GitHub Secrets and on the server .env file.

## 2. One-Time EC2 Setup

Connect to the instance:

```bash
ssh -i "RightRoute.pem" ubuntu@ec2-16-171-64-56.eu-north-1.compute.amazonaws.com
```

From your local machine, copy and run bootstrap script:

```bash
scp -i "RightRoute.pem" scripts/bootstrap-ec2.sh ubuntu@ec2-16-171-64-56.eu-north-1.compute.amazonaws.com:/tmp/bootstrap-ec2.sh
ssh -i "RightRoute.pem" ubuntu@ec2-16-171-64-56.eu-north-1.compute.amazonaws.com "bash /tmp/bootstrap-ec2.sh"
```

Open inbound security group rules for:

- Port 22 (SSH)
- Port 8001 (API)

## 3. Configure GitHub Secrets

In GitHub repo settings, add these secrets:

### Required for deployment target

- EC2_HOST = ec2-16-171-64-56.eu-north-1.compute.amazonaws.com
- EC2_USER = ubuntu
- EC2_SSH_KEY = full private key content from RightRoute.pem

### Required for app runtime

- AWS_REGION
- AWS_ACCESS_KEY
- AWS_SECRET_ACCESS_KEY
- OPENAI_API_KEY

### Optional

- AWS_S3_BUCKET
- PORT (defaults to 8001)

## 4. CI/CD Flow

Workflow file: .github/workflows/ci-cd-ec2.yml

On every push to main:

1. Install Python dependencies
2. Run syntax validation
3. Build Docker image on GitHub runner
4. Export image as artifact (.tar.gz)
4. SSH into EC2 and prepare deploy directory
5. Copy deployment compose file and image artifact to EC2 over SCP
6. Load image and restart Docker Compose service (no docker build on EC2)
7. Run health check on API root endpoint

This means EC2 does not need direct git access to your private repository.
It also avoids out-of-memory failures from building large Docker images on small EC2 instances.

## 5. Verify Deployment

After a successful workflow run:

- API root: http://<EC2_HOST>:8001/
- Swagger docs: http://<EC2_HOST>:8001/docs

## 6. Troubleshooting

If deployment fails:

- Check GitHub Actions logs for CI and SSH deploy steps
- On EC2, inspect container logs:

```bash
cd /opt/right-route-ocr
docker compose ps
docker compose logs --tail=200
```

If deployment fails at file sync, verify EC2_HOST, EC2_USER, and EC2_SSH_KEY secrets.
