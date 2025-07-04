from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_iam as iam,
    CfnOutput,
)
from constructs import Construct

class S3BucketStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Knowledge source bucket
        self.knowledge_bucket = s3.Bucket(
            self,
            "KnowledgeSourceBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.POST,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.DELETE,
                    ],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # (Optional) export the bucket ARN to CloudFormation Outputs
        CfnOutput(
            self,
            "KnowledgeBucketArn",
            value=self.knowledge_bucket.bucket_arn,
            export_name="KnowledgeBucketArn",
        )