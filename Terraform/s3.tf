resource "aws_s3_bucket" "main_bucket" {
  bucket = "${local.application_name}-bucket"
}
