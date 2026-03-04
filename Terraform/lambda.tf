locals {
  lambda_layer_root = "lambda_libraries"
}

resource "null_resource" "install_lambda_libraries" {
  provisioner "local-exec" {
    command = "pip3 install -r ${path.root}/../Resources/requirements.txt -t ${path.root}/../Resources/${local.lambda_layer_root}/python_dependency_layer/python/lib/python${var.python_version}/site-packages --only-binary=:all: --python-version ${var.python_version}"
  }
}

data "archive_file" "lambda_layer_zip" {
  depends_on = [null_resource.install_lambda_libraries]
  excludes   = ["venv"]

  type        = "zip"
  source_dir  = "${path.root}/../Resources/${local.lambda_layer_root}/python_dependency_layer"
  output_path = "${path.root}/../Resources/${local.lambda_layer_root}/python_dependency_layer.zip"
}

resource "aws_lambda_layer_version" "universal_lambda_layer" {
  layer_name               = "${local.application_name}-lambda-layer"
  s3_bucket                = aws_s3_bucket.main_bucket.bucket
  s3_key                   = aws_s3_object.lambda_layer_s3.key
  compatible_runtimes      = ["python${var.python_version}"]
  compatible_architectures = ["x86_64"]

  depends_on = [aws_s3_object.lambda_layer_s3]
}

data "archive_file" "text_embedder_lambda_files" {
  type        = "zip"
  source_dir  = "${path.root}/../Resources/text_embedder_lambda/"
  excludes    = ["requirements.txt"]
  output_path = "${path.root}/text_embedder_lambda.zip"
}

resource "aws_lambda_function" "text_embedder_lambda_function" {
  function_name = "${local.application_name}-text-embedder"

  role = aws_iam_role.general_lambda_role.arn

  filename         = data.archive_file.text_embedder_lambda_files.output_path
  source_code_hash = data.archive_file.text_embedder_lambda_files.output_base64sha256

  runtime     = "python${var.python_version}"
  handler     = "lambda_function.lambda_handler"
  timeout     = 900
  memory_size = 512

  layers = [aws_lambda_layer_version.universal_lambda_layer.arn]

  environment {
    variables = {
      S3_BUCKET         = aws_s3_bucket.main_bucket.bucket
      EMBEDDING_MODEL   = "amazon.titan-embed-text-v2:0"
      OPENSEARCH_DOMAIN = aws_opensearch_domain.rag_db.endpoint
      INDEX_NAME        = opensearch_index.products_main.name
    }
  }

  depends_on = [
    data.archive_file.text_embedder_lambda_files,
    aws_lambda_layer_version.universal_lambda_layer
  ]
}