// Parameterized Jenkins job: start an EC2 instance by ID.
// End users fill INSTANCE_ID (and optionally AWS_REGION) then click Build.
// Python runs in a Docker agent (works with stock jenkins/jenkins:lts; no apt install on controller).

pipeline {
    agent none

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
            agent any
            steps {
                script {
                    if (!params.INSTANCE_ID?.trim()) {
                        error('INSTANCE_ID is required.')
                    }
                    echo "Instance ID: ${params.INSTANCE_ID}"
                    echo "Region:      ${params.AWS_REGION}"
                    echo "Dry run:     ${params.DRY_RUN}"
                }
            }
        }

        stage('Checkout') {
            agent any
            steps {
                checkout scm
            }
        }

        stage('Start EC2 instance') {
            agent {
                docker {
                    image 'python:3.12-slim'
                    reuseNode true
                }
            }
            steps {
                dir('ec2-start-utility') {
                    withCredentials([usernamePassword(
                        credentialsId: 'aws-ec2-start',
                        usernameVariable: 'AWS_ACCESS_KEY_ID',
                        passwordVariable: 'AWS_SECRET_ACCESS_KEY'
                    )]) {
                        sh '''
                            pip install -q -r requirements.txt
                            DRY_FLAG=""
                            if [ "${DRY_RUN}" = "true" ]; then
                              DRY_FLAG="--dry-run"
                            fi
                            python start_ec2_instance.py \
                              --instance-id "${INSTANCE_ID}" \
                              --region "${AWS_REGION}" \
                              ${DRY_FLAG}
                        '''
                    }
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
