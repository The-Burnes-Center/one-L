from aws_cdk import (
    # Duration,
    Stack,
    # aws_sqs as sqs,
)
from constructs import Construct
from one_l.storage.storage_stack import S3BucketStack

class OneLStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create the storage stack as a nested stack
        self.storage_stack = S3BucketStack(self, "StorageStack")

        # Now you can access the bucket via self.storage_stack.knowledge_bucket
        
        # The code that defines your stack goes here

        # example resource
        # queue = sqs.Queue(
        #     self, "OneLQueue",
        #     visibility_timeout=Duration.seconds(300),
        # )
