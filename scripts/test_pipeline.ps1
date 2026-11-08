# End-to-end test (Windows PowerShell): upload an image, wait, check the
# thumbnail, then call the API.
# Usage: .\scripts\test_pipeline.ps1 -StackName image-pipeline -ImagePath samples\sample.jpg
param(
    [string]$StackName = "image-pipeline",
    [string]$ImagePath = "samples\sample.jpg"
)
$ErrorActionPreference = "Stop"

function Get-StackOutput([string]$Key) {
    aws cloudformation describe-stacks --stack-name $StackName `
        --query "Stacks[0].Outputs[?OutputKey=='$Key'].OutputValue" --output text
}

$Originals = Get-StackOutput "OriginalsBucketName"
$Resized   = Get-StackOutput "ResizedBucketName"
$ApiUrl    = Get-StackOutput "ResizeApiUrl"

$Name  = Split-Path $ImagePath -Leaf
$Thumb = "thumbnails/{0}-thumb{1}" -f [IO.Path]::GetFileNameWithoutExtension($Name), [IO.Path]::GetExtension($Name).ToLower()

Write-Host "1) Uploading $ImagePath to s3://$Originals/$Name"
aws s3 cp $ImagePath "s3://$Originals/$Name"

Write-Host "2) Waiting for s3://$Resized/$Thumb ..."
aws s3api wait object-exists --bucket $Resized --key $Thumb
aws s3 ls "s3://$Resized/thumbnails/" --human-readable

Write-Host "3) Starting the workflow through API Gateway"
$Body = @{ key = $Name } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri $ApiUrl -ContentType "application/json" -Body $Body

Write-Host "Done. Check the executions in the Step Functions console."
