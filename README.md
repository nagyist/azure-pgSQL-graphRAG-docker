# GraphRAG and PostgreSQL integration in docker with AGE and AI agents
This solution accelerator is designed to integrate graphRAG and PostgreSQL in a docker, with AGE added and AI agents capabilities on top. This enables Cypher queries, besides graphRAG search, vector search. Even more, AI agents can be built on top to perform advanced tasks by utilizing the rich knowledge graph.

## Background and objectives:
This solution is especially useful when:
- You have raw input data already in a DB.
- You want Cypher query capabilities.
- You prefer CLI version of graphRAG.
- You want to avoid another blob storage between the DB and graphRAG.

## Solution Accelerator Architecture
The solution is to build graphRAG index directly using the data in DB. The docker image uses postgres as base, then added python, graphRAG, AGE, semantic-kernel and other needed packages. With Apache AGE, it enables Cypher queries.
Two volumes are created to persist postgres data and app related data.

<p align="center">
<img src="project_folder/data/pics/graphrag-postgres.png" style="width:70%; height:auto; display: block; margin: 0 auto;  text-align:center">
</p>

## How to deploy the solution
Please refer to the HOWTO.pdf for detailed steps to deploy the solution:

- [HOWTO.pdf](https://github.com/Azure-Samples/postgreSQL-graphRAG-docker/blob/main/project_folder/HOWTO.pdf)

## Trademark Notice
This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow Microsoft’s Trademark & Brand Guidelines. https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks. 
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party’s policies.


