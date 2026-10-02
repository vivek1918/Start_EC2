// App team: provide URL or IP + region. DevOps can still pass INSTANCE_ID via optional override.

pipeline {
    agent any

    parameters {
        string(
            name: 'APP_URL_OR_IP',
            defaultValue: '',
            description: 'App URL (https://host/...), hostname, or IPv4 — used to find and start the EC2 instance'
        )
        string(
            name: 'INSTANCE_ID',
            defaultValue: '',
            description: 'Optional: skip URL/IP lookup and use this instance ID (DevOps only)'
        )
        choice(
            name: 'AWS_REGION',
            choices: [
                'ap-southeast-2',
                'ap-south-1'
            ],
            description: 'AWS region where the instance lives'
        )
        booleanParam(
            name: 'DRY_RUN',
            defaultValue: false,
            description: 'Resolve target only; do not start the instance'
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
                    def hasId = params.INSTANCE_ID?.trim()
                    def hasTarget = params.APP_URL_OR_IP?.trim()
                    if (!hasId && !hasTarget) {
                        error('Set APP_URL_OR_IP (for app team) or INSTANCE_ID (DevOps override).')
                    }
                    if (hasId && hasTarget) {
                        error('Set only one of APP_URL_OR_IP or INSTANCE_ID, not both.')
                    }
                    echo "App URL/IP:  ${params.APP_URL_OR_IP ?: '(not set)'}"
                    echo "Instance ID: ${params.INSTANCE_ID ?: '(not set)'}"
                    echo "Region:      ${params.AWS_REGION}"
                    echo "Dry run:     ${params.DRY_RUN}"
                }
            }
        }

        stage('Start EC2 instance') {
            steps {
                checkout scm
                withCredentials([
                    usernamePassword(
                        credentialsId: 'aws-ec2-start',
                        usernameVariable: 'AWS_ACCESS_KEY_ID',
                        passwordVariable: 'AWS_SECRET_ACCESS_KEY'
                    )
                ]) {
                    sh '''#!/usr/bin/env bash
                        set -euo pipefail
                        python3 -m venv .venv
                        . .venv/bin/activate
                        pip install -q -r requirements.txt
                        DRY_FLAG=""
                        if [ "${DRY_RUN}" = "true" ]; then
                          DRY_FLAG="--dry-run"
                        fi
                        if [ -n "${INSTANCE_ID}" ]; then
                          python start_ec2_instance.py \
                            --instance-id "${INSTANCE_ID}" \
                            --region "${AWS_REGION}" \
                            ${DRY_FLAG}
                        else
                          python start_ec2_instance.py \
                            --target "${APP_URL_OR_IP}" \
                            --region "${AWS_REGION}" \
                            ${DRY_FLAG}
                        fi
                    '''
                }
            }
        }
    }

    post {
        success {
            echo 'EC2 start request completed.'
        }
        failure {
            echo 'Failed to start instance. Check the console log.'
        }
    }
}
