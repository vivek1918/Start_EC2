# Start EC2 instance (Jenkins utility)

End-user utility: enter an **app URL or IP** (or instance ID for DevOps) in Jenkins, click Build, and the matching EC2 instance is started and its application is verified healthy.

Success means **EC2 = `running` AND `GET http://<APP_URL_OR_IP>/health` = HTTP 200**. An instance that is running but whose application never answers fails the build.

App teams do **not** need the instance ID if the URL/IP resolves to that instance’s address in AWS.

## What you get

| File | Purpose |
| --- | --- |
| `start_ec2_instance.py` | Starts the instance, waits for `running`, then waits for `/health` to return HTTP 200 |
| `Jenkinsfile` | Jenkins form: APP URL/IP and optional dry run |
| `requirements.txt` | Python library (`boto3`) |
| `sample-app/` | Small Flask test app with `GET /health` → `{"status": "UP"}` |
| `sample-app/deploy/linux/` | systemd unit + installer so the app starts on every boot |
| `scripts/ssm-run.ps1` | Run a command on the instance through Systems Manager (no SSH) |

## Application health check

After EC2 reports `running`, the script polls the application:

| Setting | CLI flag | Jenkins (`environment` block) | Default |
| --- | --- | --- | --- |
| Path | `--health-path` | `HEALTH_PATH` | `/health` |
| Timeout (seconds) | `--health-timeout` | `HEALTH_TIMEOUT` | `300` |
| Retry interval (seconds) | `--health-interval` | `HEALTH_INTERVAL` | `10` |
| Port | `--health-port` | not set | port in the URL, else 80/443 |

The health URL keeps the scheme, host, and port of `APP_URL_OR_IP` (`https://app.example.com:8443` → `https://app.example.com:8443/health`, `203.0.113.10` → `http://203.0.113.10/health`). With `--instance-id`, the instance's public IP is used.

If `APP_URL_OR_IP` is a **private IP** and the instance also has a public IP, each attempt tries the private IP first and then the public IP. A private IP is only reachable from inside the VPC (e.g. Jenkins running in AWS), so Jenkins outside AWS passes via the public IP. An instance with only a private IP must be checked from inside the VPC.

The script does **not** start the application itself. The application must start on its own when the instance boots, using whatever mechanism its OS provides (systemd on Linux; e.g. a Windows service on Windows). That keeps the script and Jenkins job OS-independent: no OS parameter is needed.

`DRY_RUN` prints the planned actions (start, wait, which health URL would be polled) and exits without starting EC2 or calling the health endpoint.

## Sample application (Linux, systemd)

### Standard access setup

| Purpose | How | Security group inbound rule |
| --- | --- | --- |
| Health check (Jenkins/script) | HTTP to `/health` | **HTTP, TCP 80, Anywhere-IPv4** (`0.0.0.0/0`) |
| Admin commands on the instance | AWS Systems Manager (no SSH) | **none** (no port 22, no 443) |

Systems Manager connects outbound from the instance, so no inbound admin port or IP allow-list is needed and nothing breaks when your internet IP changes.

One-time Systems Manager setup:

1. **IAM → Roles → Create role** → trusted entity **AWS service / EC2** → attach policy **`AmazonSSMManagedInstanceCore`** → name it e.g. `ec2-ssm-role`.
2. **EC2 → select instance → Actions → Security → Modify IAM role** → choose `ec2-ssm-role` → **Update**.
3. Wait 2–5 minutes, then check **Systems Manager → Fleet Manager**: the instance should show **Online**. (Amazon Linux 2023 and Ubuntu AMIs already include the SSM agent.)
4. Run commands from PowerShell (needs `aws login`):

```powershell
.\scripts\ssm-run.ps1 -InstanceId i-0da98ca5ffe3a0edf -Command "sudo systemctl is-active sample-app"
.\scripts\ssm-run.ps1 -InstanceId i-0da98ca5ffe3a0edf -Command "sudo systemctl stop sample-app"
.\scripts\ssm-run.ps1 -InstanceId i-0da98ca5ffe3a0edf -Command "sudo journalctl -u sample-app -n 50 --no-pager"
```

   For an interactive shell, use **EC2 → Connect → Session Manager** in the console.

5. Once SSM works, **delete the SSH (22) and HTTPS (443) inbound rules**.

### Installing the sample app

One-time setup on the test instance:

1. **Security group:** see the standard above (HTTP 80 inbound).
2. **Elastic IP (recommended):** auto-assigned public IPs change on stop/start; an Elastic IP keeps `APP_URL_OR_IP` stable.
3. **Copy and install** (from PowerShell on your PC; user is `ec2-user` on Amazon Linux, `ubuntu` on Ubuntu). This initial copy uses SSH, so it needs a temporary SSH rule from **My IP**; remove it afterwards:

```powershell
cd c:\workspace\devops\ec2-start-utility
scp -i C:\path\to\key.pem -r sample-app ec2-user@<PUBLIC_IP>:~/
ssh -i C:\path\to\key.pem ec2-user@<PUBLIC_IP> "sudo bash ~/sample-app/deploy/linux/install.sh 80"
```

The installer:

- installs Python and **MariaDB** (MySQL-compatible) if needed, binds MariaDB to `127.0.0.1`, and enables it at boot;
- creates database `sampleapp` and user `sampleapp` with a random password (kept on re-runs);
- puts the app in `/opt/sample-app` (virtualenv + gunicorn) and writes port + DB settings to `/etc/sample-app/sample-app.env` (mode `0600`, root only);
- enables the `sample-app` systemd service at boot, starts it, and verifies `http://127.0.0.1:<port>/health` locally.

Re-run it to update the app or change the port.

**The app never runs without its database:**

| Layer | Behaviour |
| --- | --- |
| systemd `Requires=`/`After=mariadb.service` | App starts after MariaDB; `systemctl stop mariadb` also stops the app; starting MariaDB starts the app again |
| `ExecStartPre=app.py --check-db` | Waits up to `DB_WAIT_TIMEOUT` (60s) for the DB; if unreachable the app is not started and systemd retries every 5s |
| gunicorn `--preload "app:create_app()"` | App creation needs the DB (creates the `visits` table); fails if unreachable |
| `GET /health` | Runs a DB query: `200 {"status":"UP","database":"UP"}`, or `503 {"status":"DOWN","database":"DOWN"}` → Jenkins fails |

`GET /` writes a row to the `visits` table and returns the visit count, showing real reads/writes.

Useful commands on the instance:

```bash
systemctl status sample-app mariadb
journalctl -u sample-app -f
curl http://127.0.0.1/health
sudo systemctl stop mariadb      # app stops too; /health unreachable
sudo systemctl start mariadb     # app starts again automatically
```

4. **Verify auto-start:** stop the instance in the AWS console, then run the Jenkins job (or the script). It should start EC2 and pass the health check without anyone logging in to start the app.

## If you have no AWS account or Jenkins yet

This script talks to **your** AWS account. It cannot start machines without credentials.

Ask your company admin (or create a personal AWS account for learning) for:

1. The **app URL or IP** you use to reach the server (or an instance ID for admins)
2. The **AWS region** where that instance runs (e.g. `ap-south-1`, `ap-southeast-2`)
3. **AWS access keys** with permission to start that instance, **or** a Jenkins server that already has an IAM role

Minimum IAM permission for the user/role Jenkins uses:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:StartInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeNetworkInterfaces"
      ],
      "Resource": "*"
    }
  ]
}
```

In production, restrict `Resource` to the specific instance ARN instead of `"*"`.

## Run locally (no Jenkins)

```bash
cd devops/ec2-start-utility
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=ap-south-1

python start_ec2_instance.py --target https://myapp.example.com --region ap-south-1
python start_ec2_instance.py --target 203.0.113.10 --region ap-south-1
python start_ec2_instance.py --instance-id i-0123456789abcdef0 --region ap-south-1
```

Check resolution without starting:

```bash
python start_ec2_instance.py --target 203.0.113.10 --region ap-south-1 --dry-run
```

### How URL/IP lookup works

**IP address input:** matched against EC2 public or private IPs in the chosen **region** (`DescribeInstances`).

**URL or hostname input:**

1. DNS lookup. If the name is a CNAME to an AWS managed service (`*.elb.amazonaws.com`, `*.cloudfront.net`, API Gateway, Global Accelerator, App Runner, Amplify, S3 website, Lambda URL), it is rejected.
2. Only **public** IPs are used; a URL resolving only to private IPs is rejected.
3. Each public IP is looked up with `DescribeNetworkInterfaces`. The IP must belong to a regular network interface attached to an EC2 instance. IPs owned by an **ALB, NLB, Classic/Gateway LB, NAT gateway**, or other AWS managed interface are rejected, as are IPs not found in the account/region (e.g. CloudFront, external hosting).
4. Exactly **one** EC2 instance → start it.

**Limits:** auto-assigned public IPs are released when an instance stops, so a URL only works for a stopped instance if its DNS points at an **Elastic IP**. Otherwise use the instance ID.

Windows PowerShell env vars:

```powershell
$env:AWS_ACCESS_KEY_ID = "..."
$env:AWS_SECRET_ACCESS_KEY = "..."
$env:AWS_DEFAULT_REGION = "ap-south-1"
python start_ec2_instance.py --instance-id i-0123456789abcdef0
```

## Jenkins frontend (what the end user sees)

1. Open Jenkins.
2. Open the job **Start EC2 Instance**.
3. Click **Build with Parameters**.
4. Fill:
   - `APP_URL_OR_IP` — e.g. `https://dev.mycompany.com` or `203.0.113.10` (app team)
   - `INSTANCE_ID` — leave empty unless DevOps overrides lookup
   - `AWS_REGION` — region where the instance lives
   - `DRY_RUN` — leave unchecked to actually start
5. Click **Build**.
6. Open **Console Output** to see previous state → start → `running` → health check attempts → `Application is healthy.` The build fails if `/health` does not return HTTP 200 within the timeout.

### One-time Jenkins job setup (admin)

1. Install the **Pipeline** plugin if it is not already present.
2. **Manage Jenkins → Credentials → Add**:
   - Kind: Username with password
   - ID: `aws-ec2-start` (must match the Jenkinsfile)
   - Username: AWS Access Key ID
   - Password: AWS Secret Access Key
3. **New Item → Pipeline**:
   - Name: `Start EC2 Instance`
   - Definition: Pipeline script from SCM
   - Script Path: `ec2-start-utility/Jenkinsfile` (when the git root is the `devops` folder)
4. The agent that runs the job needs `python3` and `pip` (included in the Docker image below).

If your Jenkins controller runs on **Windows without Linux agents**, use the Docker setup below (recommended) or replace `sh` steps with `bat` / PowerShell.

### Local Jenkins (Docker on Windows)

You already proved AWS works from PowerShell; Jenkins will run the same Python script with stored credentials.

1. **Start Jenkins** (from PowerShell):

```powershell
cd c:\workspace\devops\ec2-start-utility\jenkins
.\setup-jenkins.ps1
```

2. Open **http://localhost:8080**, paste the **initial admin password** from the script output, install suggested plugins, create an admin user.

3. **Manage Jenkins → Credentials → (System) → Global credentials → Add Credentials**:
   - Kind: **Username with password**
   - ID: **`aws-ec2-start`** (exactly this ID)
   - Username: your **AWS Access Key ID**
   - Password: your **AWS Secret Access Key**

4. **New Item → Pipeline** → name **`Start EC2 Instance`**:
   - **Pipeline → Definition:** Pipeline script from SCM
   - **SCM:** Git
   - **Script Path:** `Jenkinsfile` for [Start_EC2](https://github.com/vivek1918/Start_EC2) (files at repo root). Use `ec2-start-utility/Jenkinsfile` only if your git root is the parent `devops` folder.

   **Option A — code on GitHub/GitLab (branch `main`)** *(use this if you pushed your repo)*:

   | Field | Value |
   | --- | --- |
   | Repository URL | e.g. `https://github.com/vivek1918/Start_EC2.git` |
   | Credentials | Add if the repo is private (PAT or username/password) |
   | Branch Specifier | **`*/main`** |

   **Option B — local Docker only (`file:///repo`)**:

   | Field | Value |
   | --- | --- |
   | Repository URL | `file:///repo` (bare clone from `setup-jenkins.ps1`) |
   | Branch Specifier | **`*/main`** (must match `git branch` in `c:\workspace\devops`) |

   If you see `couldn't find remote ref refs/heads/main`, the job branch does not match the repo. For Option B run `.\sync-bare-repo.ps1`. For Option A confirm the default branch on GitHub is `main`.

   After local code changes (Option B only), commit then:

```powershell
cd c:\workspace\devops\ec2-start-utility\jenkins
.\sync-bare-repo.ps1
```

5. **Build with Parameters**:
   - `INSTANCE_ID` — your `i-…` id
   - `AWS_REGION` — e.g. `ap-south-1`
   - `DRY_RUN` — unchecked to start

6. Open **Console Output** — you should see the same messages as running `start_ec2_instance.py` locally.

**Stop Jenkins when done:**

```powershell
cd c:\workspace\devops\ec2-start-utility\jenkins
docker compose down
```

(Data is kept in the Docker volume `jenkins_home` until you remove it with `docker compose down -v`.)

## What the script does

1. Finds the EC2 instance from `APP_URL_OR_IP` (or validates the instance ID).
2. Reads current EC2 state.
3. If `stopped` (or finishes `stopping`), calls `StartInstances`; if already `running`, skips the start.
4. Waits until AWS reports `running`.
5. Polls `<APP_URL_OR_IP>/health` until HTTP 200, or fails after the health timeout.

It does not create instances, stop them, change security groups, or start the application.
