pipeline {
    agent any

    parameters {
        string(
            name: 'APP_URL_OR_IP',
            defaultValue: '',
            description: 'Enter the application URL or IPv4 address'
        )

        choice(
            name: 'AWS_REGION',
            choices: [
                'ap-southeast-2',
                'ap-south-1'
            ],
            description: 'AWS region where the application EC2 instance is running'
        )

        booleanParam(
            name: 'DRY_RUN',
            defaultValue: false,
            description: 'Find the EC2 instance but do not start it'
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

        stage('Validate Input') {
            steps {
                script {
                    if (!params.APP_URL_OR_IP?.trim()) {
                        error('Application URL or IP address is required.')
                    }

                    echo "Application URL/IP: ${params.APP_URL_OR_IP}"
                    echo "AWS Region: ${params.AWS_REGION}"
                    echo "Dry Run: ${params.DRY_RUN}"
                }
            }
        }

        stage('Start EC2 Instance') {
            steps {
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

                        python start_ec2_instance.py \
                            --target "${APP_URL_OR_IP}" \
                            --region "${AWS_REGION}" \
                            ${DRY_FLAG}
                    '''
                }
            }
        }
    }

    post {
        success {
            echo 'EC2 start request completed successfully.'
        }

        failure {
            echo 'Failed to start the EC2 instance. Check the console output.'
        }
    }
}