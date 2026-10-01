// Parameterized Jenkins job: start an EC2 instance by ID.
// Works without the "Docker Pipeline" plugin (uses `docker run` + python image).
// Repo layout: https://github.com/vivek1918/Start_EC2 (Jenkinsfile at repo root).

pipeline {
    agent any

    parameters {
        string(
            name: 'INSTANCE_ID',
            defaultValue: '',
            description: 'EC2 instance ID to start, for example i-0123456789abcdef0'
        )
        string(
            name: 'AWS_REGION',
            defaultValue: 'ap-south-1',
            description: 'AWS region where the instance lives'
        )
        booleanParam(
            name: 'DRY_RUN',
            defaultValue: false,
            description: 'Validate only; do not start the instance'
        )
    }

    options {
        timestamps()
        timeout(time: 15, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    environment {
        PYTHONUNBUFFERED = '1'
    }

    stages {
        stage('Validate input') {
            steps {
                script {
                    if (!params.INSTANCE_ID?.trim()) {
                        error('INSTANCE_ID is required. Use Build with Parameters and set INSTANCE_ID.')
                    }
                    echo "Instance ID: ${params.INSTANCE_ID}"
                    echo "Region:      ${params.AWS_REGION}"
                    echo "Dry run:     ${params.DRY_RUN}"
                }
            }
        }

        stage('Start EC2 instance') {
            steps {
                checkout scm
                withCredentials([usernamePassword(
                    credentialsId: 'aws-ec2-start',
                    usernameVariable: 'AWS_ACCESS_KEY_ID',
                    passwordVariable: 'AWS_SECRET_ACCESS_KEY'
                )]) {
                    sh '''#!/usr/bin/env bash
                        set -euo pipefail
                        DRY_FLAG=""
                        if [ "${DRY_RUN}" = "true" ]; then
                          DRY_FLAG="--dry-run"
                        fi
                        if ! command -v docker >/dev/null 2>&1; then
                          echo "docker CLI not found on Jenkins agent. Install Docker Pipeline plugin OR use jenkins/docker-compose with the provided Dockerfile." >&2
                          exit 1
                        fi
                        docker run --rm \
                          -e AWS_ACCESS_KEY_ID \
                          -e AWS_SECRET_ACCESS_KEY \
                          -e INSTANCE_ID="${INSTANCE_ID}" \
                          -e AWS_REGION="${AWS_REGION}" \
                          -e DRY_RUN="${DRY_RUN}" \
                          -v "${WORKSPACE}:/work" -w /work \
                          python:3.12-slim \
                          bash -lc 'pip install -q -r requirements.txt && DRY_FLAG="" && [ "$DRY_RUN" = "true" ] && DRY_FLAG="--dry-run"; python start_ec2_instance.py --instance-id "$INSTANCE_ID" --region "$AWS_REGION" $DRY_FLAG'
                    '''
                }
            }
        }
    }

    post {
        success {
            echo "Instance ${params.INSTANCE_ID} start request completed."
        }
        failure {
            echo "Failed to start instance ${params.INSTANCE_ID}. Check the console log."
        }
    }
}
