# ahmed-bshg-case-study

## Overview

### Solution Summary

This repository contains the solution for the Senior Data Scientist Case Study for BSHG solved by me, Ahmed Abunada. Overall the problem is the development and building of a RAG system that is used to communicate with product data and avoid any hallucinations when retrieving data. My Solution is a **complete cloud** implementation, meaning the entire end to end project leverages and runs on AWS, using resources, processes and technologies to implement successfully.

The solution works to gain the most information from 


### Repository Structure


### Data Handling & Processing

Data is processed and cleaned in stages before being indexed in OpenSearch. The approach is split into four areas: exploratory analysis, text, images, and videos.

#### EDA

An exploratory analysis was done to understand missingness and data quality. Missing values per column were computed (treating empty lists as null where relevant) and visualised to decide which columns to keep or drop.

![Missing datapoints per column](Data/newplot-23.png)

The plot shows that **bought_together** is fully empty, and that **videos** and **price** have high missingness. **bought_together** was dropped from the pipeline because it adds no signal. Remaining columns were kept and handled in the text/image/video pipelines (e.g. optional fields, null-safe serialisation).

#### Text Data

Product text is turned into a single JSON document per product and then embedded for semantic search.

- **Processing:** Each row is mapped to a JSON document with fields such as `parent_asin`, `title`, `main_category`, `store`, `price`, `average_rating`, `rating_number`, `features`, `description`, `categories`, and `details`. The text used for embedding is built by concatenating title, main category, features, and description.
- **Embedding:** The concatenated text is embedded with the **Amazon Titan Embed Text v2** model (`amazon.titan-embed-text-v2:0`) with 1024 dimensions and normalisation. The resulting vector is stored in the document and indexed in the text OpenSearch index for KNN search.

#### Image Data

Images are not embedded directly; they are first described in text, then that text is embedded.

- **Description:** Each product image (e.g. the `MAIN` variant) is sent to **Amazon Nova Lite** with a prompt asking for a detailed product description (appearance, colours, materials, notable visual features). The model returns a short text description.
- **Storage:** For each image we store a JSON document with `parent_asin`, `variant`, the Nova-generated `description`, and an `embedding` field. Documents are written to the image OpenSearch index in the same way as text (e.g. bulk indexing with chunking).
- **Embedding:** The image description text is embedded with the **Amazon Titan Embed Text v2** model (same as text), so image and text data live in a shared embedding space and can be queried with the same semantic search setup.

#### Video Data

Video ingestion was not implemented in this solution.

- **Reason:** Processing and describing video would cost a lot of money for just this case study, hence I skipped it.
- **Planned approach:** Videos would be described with a multimodal model such as **Pegasus** on AWS, with chunking for long videos. The resulting text would be embedded with the **Titan Embed Text v2** model and stored in OpenSearch in the same way as text and image documents, so the agent could search them via the same KNN interface.

## Implementation

### Summary

### Solution Architecture
![bshg.drawio.png](Data/bshg.drawio.png)

### Architecture Components

#### 1. Vector Database

The vector database is AWS OpenSearch Service. The agent embeds the user question with Titan, then runs KNN search so that the query vector is compared against document embeddings in the index; the nearest neighbours by inner product are returned as context. There are three indices: products_main for product text and metadata, products_images for image descriptions, and products_videos for future video derived text.

**Text index (products_main)**

- Holds product metadata and text: title, category, features, description, price, ratings.

```json
{
  "parent_asin": "B08N5WRWNW",
  "title": "Sony WH-1000XM4 Wireless Headphones",
  "store": "Sony",
  "main_category": "Electronics",
  "categories": ["Electronics", "Headphones"],
  "price": 279.99,
  "average_rating": 4.7,
  "rating_number": 52341,
  "features": "Noise cancellation, 30h battery",
  "description": "Premium wireless headphones...",
  "details": { "brand": "Sony", "color": "Black" },
  "embedding": [0.02, -0.41, 0.73, "..."]
}
```

**Image index (products_images)**

- One document per product image; Nova Lite generates the description text, then Titan embeds it.

```json
{
  "parent_asin": "B08N5WRWNW",
  "variant": "MAIN",
  "description": "Black over-ear headphones, cushioned ear cups, Sony branding.",
  "embedding": [0.01, -0.30, 0.54, "..."]
}
```

**Video index (products_videos)**

- Reserved for future video pipeline; same embedding setup as text and image indices.

```json
{
  "parent_asin": "B08N5WRWNW",
  "title": "Unboxing and Setup",
  "duration": 342,
  "user_id": "USR_A3X9KLP",
  "description": "Unboxing, pairing demo, noise cancellation test.",
  "embedding": [0.03, -0.27, 0.59, "..."]
}
```

#### 2. Data Processing Step Function

![img.png](Data/img.png)

## Future Improvements

### Security

### Prompts

### Automation

### Agent Session Memory

### Videos & Image Processing