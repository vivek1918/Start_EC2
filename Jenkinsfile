pipeline {
    agent any

    parameters {
        string(
            name: 'APP_URL_OR_IP',
            defaultValue: '',
            description: 'Enter the application URL or IPv4 address'
        )

        string(
            name: 'HEALTH_PATH',
            defaultValue: '/health',
            description: 'Application health endpoint. sample-app: /health, core-backend (Spring Boot): /actuator/health'
        )

        string(
            name: 'HEALTH_PORT',
            defaultValue: '',
            description: 'Health check port. Empty = port in APP_URL_OR_IP, else 80. core-backend: 8082'
        )

        string(
            name: 'HEALTH_TIMEOUT',
            defaultValue: '600',
            description: 'Seconds to wait for the application (and its dependencies) to report healthy'
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

        // Success requires HTTP 200 from http://<APP_URL_OR_IP>[:HEALTH_PORT]<HEALTH_PATH>.
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
                    if (params.HEALTH_PORT?.trim() && !(params.HEALTH_PORT.trim() ==~ /\d{1,5}/)) {
                        error('HEALTH_PORT must be a number, e.g. 8082, or empty.')
                    }

                    if (!(params.HEALTH_TIMEOUT?.trim() ==~ /\d+/)) {
                        error('HEALTH_TIMEOUT must be a number of seconds.')
                    }

                    echo "Health check: ${params.HEALTH_PATH} port ${params.HEALTH_PORT?.trim() ?: 'default'} (timeout ${params.HEALTH_TIMEOUT}s, every ${env.HEALTH_INTERVAL}s)"                }
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

                        EXTRA_FLAGS=""

                        if [ "${DRY_RUN}" = "true" ]; then
                            EXTRA_FLAGS="--dry-run"
                        fi

                        if [ -n "${HEALTH_PORT// /}" ]; then
                            EXTRA_FLAGS="${EXTRA_FLAGS} --health-port ${HEALTH_PORT// /}"
                        fi

                        python start_ec2_instance.py \
                            --target "${APP_URL_OR_IP}" \
                            --health-path "${HEALTH_PATH}" \
                            --health-timeout "${HEALTH_TIMEOUT}" \
                            --health-interval "${HEALTH_INTERVAL}" \
                            ${EXTRA_FLAGS}
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
                    echo 'EC2 instance is running, dependencies are connected, and the application is healthy (HTTP 200).'
                }
            }
        }

        failure {
            echo 'Failed: the EC2 instance did not start or the application did not become healthy. Check the console output.'
        }
    }
}
