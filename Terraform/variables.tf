variable "aws_region" {
  description = "Region AWS Resources will be created"
  default     = "eu-central-1"
}

variable "python_version" {
  description = "Version of python used by lambda functions"
  default     = "3.12"
}

variable "s3_data_key" {
  description = "Location of data files in s3 bucket"
  default     = "data/"
}