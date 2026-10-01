# Start EC2 instance (Jenkins utility)

End-user utility: enter an EC2 instance ID in Jenkins, click Build, and the instance is started (`running`).

You do **not** need to know DevOps to use it after someone sets Jenkins and AWS up once.

## What you get

| File | Purpose |
| --- | --- |
| `start_ec2_instance.py` | Starts the instance and waits until state is `running` |
| `Jenkinsfile` | Jenkins form: Instance ID, region, optional dry run |
| `requirements.txt` | Python library (`boto3`) |

## If you have no AWS account or Jenkins yet

This script talks to **your** AWS account. It cannot start machines without credentials.

Ask your company admin (or create a personal AWS account for learning) for:

1. An **EC2 instance ID** that you are allowed to start (looks like `i-0123456789abcdef0`)
2. The **AWS region** (India Mumbai is often `ap-south-1`)
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
        "ec2:DescribeInstances"
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

python start_ec2_instance.py --instance-id i-0123456789abcdef0
```

Check arguments without starting anything:

```bash
python start_ec2_instance.py --instance-id i-0123456789abcdef0 --dry-run
```

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
   - `INSTANCE_ID` — required
   - `AWS_REGION` — default `ap-south-1`
   - `DRY_RUN` — leave unchecked to actually start
5. Click **Build**.
6. Open **Console Output** to see previous state → start → `running`, plus IP if AWS returns one.

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
   - **Repository URL:** `file:///repo`
   - **Branch:** `*/master` (default after `setup-jenkins.ps1`; run `git branch` in `c:\workspace\devops` if unsure)
   - **Script Path:** `ec2-start-utility/Jenkinsfile`

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

1. Checks the instance ID format.
2. Reads current EC2 state.
3. If already `running`, exits successfully.
4. If `stopped` (or finishes `stopping`), calls `StartInstances`.
5. Waits until AWS reports `running`.

It does not create instances, stop them, or change security groups.
