#!/usr/bin/env bash
# End-to-end test: upload an image, wait, check the thumbnail, then call the API.
# Usage: ./scripts/test_pipeline.sh [stack-name] [image-path]
set -euo pipefail

STACK="${1:-image-pipeline}"
IMAGE="${2:-samples/sample.jpg}"
NAME="$(basename "$IMAGE")"
THUMB="thumbnails/${NAME%.*}-thumb.${NAME##*.}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

ORIGINALS="$(output OriginalsBucketName)"
RESIZED="$(output ResizedBucketName)"
API_URL="$(output ResizeApiUrl)"

echo "1) Uploading $IMAGE to s3://$ORIGINALS/$NAME"
aws s3 cp "$IMAGE" "s3://$ORIGINALS/$NAME"

echo "2) Waiting for s3://$RESIZED/$THUMB ..."
aws s3api wait object-exists --bucket "$RESIZED" --key "$THUMB"
aws s3 ls "s3://$RESIZED/thumbnails/" --human-readable

echo "3) Starting the workflow through API Gateway"
curl -s -X POST "$API_URL" -H "Content-Type: application/json" -d "{\"key\": \"$NAME\"}"
echo
echo "Done. Check the executions in the Step Functions console."
