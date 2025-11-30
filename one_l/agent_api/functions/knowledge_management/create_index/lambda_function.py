"""
Lambda function for creating OpenSearch Serverless vector index.
This function handles the creation of the vector index required by Bedrock Knowledge Base.
Uses opensearch-py library with proper AWS authentication.
"""

import os
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
import json
import time
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Lambda function to create vector index in OpenSearch Serverless collection.
    
    Expected environment variables:
    - COLLECTION_ENDPOINT: OpenSearch collection endpoint
    - INDEX_NAME: Name of the index to create (default: knowledge-base-index)
    - EMBEDDING_DIM: Embedding dimension (default: 1024)
    - REGION: AWS region
    """
    
    try:
        logger.info(f"Create index request received: {json.dumps(event, default=str)}")
        
        # Get configuration from environment variables
        host = os.environ.get("COLLECTION_ENDPOINT")
        collection_name = os.environ.get("COLLECTION_NAME")
        index_name = os.environ.get("INDEX_NAME", "knowledge-base-index")
        embedding_dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
        region = os.environ.get("REGION")
        
        # Resolve endpoint from name if not provided
        if not host and collection_name:
            try:
                aoss_client = boto3.client('opensearchserverless')
                logger.info(f"Resolving endpoint for collection: {collection_name}")
                response = aoss_client.list_collections(
                    collectionFilters={'name': collection_name}
                )
                for collection in response.get('collectionSummaries', []):
                    if collection.get('name') == collection_name:
                        collection_id = collection.get('id')
                        host = f"{collection_id}.{region}.aoss.amazonaws.com"
                        logger.info(f"Resolved endpoint: {host}")
                        break
            except Exception as e:
                logger.error(f"Failed to resolve endpoint for collection {collection_name}: {e}")
        
        logger.info(f"Collection Endpoint: {host}")
        logger.info(f"Index name: {index_name}")
        logger.info(f"Embedding dimension: {embedding_dim}")
        logger.info(f"Region: {region}")
        
        if not host:
            raise ValueError("COLLECTION_ENDPOINT environment variable is required")
        if not region:
            raise ValueError("REGION environment variable is required")
        
        # Define the index configuration matching the working implementation
        payload = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 512
                }
            },
            "mappings": {
                "properties": {
                    "vector_field": {
                        "type": "knn_vector",
                        "dimension": embedding_dim,
                        "method": {
                            "name": "hnsw",
                            "space_type": "innerproduct",
                            "engine": "faiss",
                            "parameters": {
                                "ef_construction": 512,
                                "m": 16
                            }
                        }
                    },
                    "metadata_field": {
                        "type": "text", 
                        "index": False
                    },
                    "text_field": {
                        "type": "text"
                    }
                }
            }
        }
        
        # Set up AWS authentication
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, 'aoss')
        
        # Create OpenSearch client
        client = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20,
        )
        
        # Create the index
        logger.info(f"Creating index {index_name} with payload: {json.dumps(payload, indent=2)}")
        
        try:
            response = client.indices.create(index_name, body=json.dumps(payload))
            logger.info(f"Index creation response: {response}")
            
            # Wait for index to be ready (matching working implementation)
            logger.info("Waiting 60 seconds for index to be fully ready...")
            time.sleep(60)
            
            logger.info(f"Successfully created index {index_name}")
            return {
                "Status": "SUCCESS",
                "Data": {
                    "IndexName": index_name,
                    "Message": f"Index {index_name} created successfully"
                }
            }
            
        except Exception as e:
            error_msg = str(e)
            if "resource_already_exists_exception" in error_msg.lower() or "already exists" in error_msg.lower():
                logger.info(f"Index {index_name} already exists, treating as success")
                return {
                    "Status": "SUCCESS", 
                    "Data": {
                        "IndexName": index_name,
                        "Message": f"Index {index_name} already exists"
                    }
                }
            else:
                logger.error(f"Index creation failed: {error_msg}")
                raise e
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            "Status": "FAILED",
            "Reason": f"Failed to create index: {str(e)}"
        } 