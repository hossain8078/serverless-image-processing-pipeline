"""Test setup.

boto3 ships with the Lambda runtime and is listed in requirements-dev.txt.
If it is not installed locally, a tiny stand-in is registered so the unit
tests (which stub S3 anyway) can still run.
"""

import sys
import types

try:
    import boto3  # noqa: F401
    import botocore.exceptions  # noqa: F401
except ImportError:
    class ClientError(Exception):
        def __init__(self, error_response, operation_name):
            super().__init__(f"{operation_name}: {error_response}")
            self.response = error_response

    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *args, **kwargs: None
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    botocore.exceptions = exceptions
    sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exceptions})
