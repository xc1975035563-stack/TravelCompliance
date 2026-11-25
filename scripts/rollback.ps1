param(
    [string]$Version = "1.0.0"
)

$image = "travel-compliance:$Version"
docker image inspect $image | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "本地没有镜像 $image。请先在该版本构建并打上标签。"
}
$env:APP_VERSION = $Version
docker compose up -d
Write-Host "已按镜像标签 $image 启动。"
