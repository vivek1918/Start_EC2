pipeline {
    agent any

    parameters {
        string(
            name: 'APP_URL_OR_IP',
            defaultValue: '',
            description: 'Enter the application URL or IPv4 address'
        )

        booleanParam(
            name: 'DRY_RUN',
            defaultValue: false,
            description: 'Find the EC2 instance and show planned actions, but do not start it or check the application'
        )
    }

    options {
        timestamps()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    environment {
        PYTHONUNBUFFERED = '1'
        AWS_DEFAULT_REGION = 'ap-southeast-2'

        // Application health check: success requires HTTP 200 from
        // http://<APP_URL_OR_IP><HEALTH_PATH> before HEALTH_TIMEOUT seconds.
        HEALTH_PATH = '/health'
        HEALTH_TIMEOUT = '300'
        HEALTH_INTERVAL = '10'
    }

    stages {

        stage('Validate Input') {
            steps {
                script {
                    if (!params.APP_URL_OR_IP?.trim()) {
                        error('Application URL or IP address is required.')
                    }

                    echo "Application URL/IP: ${params.APP_URL_OR_IP}"
                    echo "AWS Region: ${env.AWS_DEFAULT_REGION}"
                    echo "Dry Run: ${params.DRY_RUN}"
                    echo "Health check: ${env.HEALTH_PATH} (timeout ${env.HEALTH_TIMEOUT}s, every ${env.HEALTH_INTERVAL}s)"
                }
            }
        }

        stage('Start EC2 and Verify Application') {
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
                            --health-path "${HEALTH_PATH}" \
                            --health-timeout "${HEALTH_TIMEOUT}" \
                            --health-interval "${HEALTH_INTERVAL}" \
                            ${DRY_FLAG}
                    '''
                }
            }
        }
    }

    post {
        success {
            script {
                if (params.DRY_RUN) {
                    echo 'Dry run completed. No EC2 or application changes were made.'
                } else {
                    echo 'EC2 instance is running and the application is healthy (HTTP 200).'
                }
            }
        }

        failure {
            echo 'Failed: the EC2 instance did not start or the application did not become healthy. Check the console output.'
        }
    }
}
