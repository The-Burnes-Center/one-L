import aws_cdk as core
import aws_cdk.assertions as assertions

from one_l.one_l_stack import OneLStack

# example tests. To run these tests, uncomment this file along with the example
# resource in one_l/one_l_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = OneLStack(app, "one-l")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
