from couchbase.options import LOCKMODE_NONE
from acouchbase.cluster import Cluster
from couchbase.cluster import PasswordAuthenticator, ClusterTimeoutOptions, QueryOptions
from couchbase.exceptions import DocumentNotFoundException
import couchbase.subdocument as SD
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from pydantic import BaseModel
from typing import List
from distutils.util import strtobool
from datetime import timedelta
import os
import base64

cb_host = os.environ['COUCHBASE_HOST'] if os.environ.get('COUCHBASE_HOST') else "localhost"
user_name = os.environ['COUCHBASE_USER'] if os.environ.get('COUCHBASE_USER') else "Administrator"
user_pass = os.environ['COUCHBASE_PASSWORD'] if os.environ.get('COUCHBASE_PASSWORD') else "password"
bucket_name = os.environ['COUCHBASE_BUCKET'] if os.environ.get('COUCHBASE_BUCKET') else "sample_app"
net_setting = os.environ['COUCHBASE_NETWORK'] if os.environ.get('COUCHBASE_NETWORK') else "False"
use_ssl = os.environ['COUCHBASE_TLS'] if os.environ.get('COUCHBASE_TLS') else "True"
scope_name = 'profiles'
net_arg = bool(strtobool(net_setting))
tls_arg = bool(strtobool(use_ssl))
auth_token = {}
cluster = {}
collections = {}

cb_authenticator = PasswordAuthenticator(user_name, user_pass)
cb_timeouts = ClusterTimeoutOptions(query_timeout=timedelta(seconds=30), kv_timeout=timedelta(seconds=30))

if net_arg:
    net_string = 'external'
else:
    net_string = 'default'
if tls_arg:
    connect_opt = "?ssl=no_verify&config_total_timeout=15&config_node_timeout=10&network=" + net_string
    connect_str = "couchbases://" + cb_host + connect_opt
else:
    connect_opt = "?config_total_timeout=15&config_node_timeout=10&network=" + net_string
    connect_str = "couchbase://" + cb_host + connect_opt


async def get_collection(cb_cluster, collection_name):
    bucket = cb_cluster.bucket(bucket_name)
    await bucket.on_connect()
    scope = bucket.scope(scope_name)
    collection = scope.collection(collection_name)
    await collection.on_connect()
    return collection


async def get_cluster():
    cluster = Cluster(connect_str, authenticator=cb_authenticator, lockmode=LOCKMODE_NONE, timeout_options=cb_timeouts)
    await cluster.on_connect()
    return cluster


def verify_token(req: Request):
    token = ''
    if auth_token[1]:
        if 'Authorization' in req.headers:
            token = req.headers["Authorization"]
        if token.startswith('Bearer '):
            token = token[len('Bearer '):]
        if token != auth_token[1]:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized"
            )
    return True


class Profile(BaseModel):
    record_id: int
    name: str
    nickname: str
    picture: int
    user_id: str
    email: str
    email_verified: bool
    first_name: str
    last_name: str
    address: str
    city: str
    state: str
    zip_code: str
    phone: str
    date_of_birth: str


class Image(BaseModel):
    record_id: int
    type: str
    image: str


async def get_profile(collection, collection_name: str, document: str):
    try:
        doc_id = f"{collection_name}:{document}"
        result = await collection.get(doc_id)
    except DocumentNotFoundException:
        raise HTTPException(
            status_code=404,
            detail="Not Found"
        )
    return result.content_as[dict]


async def query_profiles(cluster, collection_name: str, field: str, value: str):
    contents = []
    keyspace = f"{bucket_name}.{scope_name}.{collection_name}"
    query = f"SELECT * FROM {keyspace} WHERE {field} = \"{value}\";"
    result = cluster.query(query, QueryOptions(metrics=False, adhoc=False))
    async for item in result:
        contents.append(item[collection_name])
    if len(contents) == 0:
        raise HTTPException(
            status_code=404,
            detail="Not Found"
        )
    return contents


async def get_image_data(record):
    try:
        codec = record['type']
        image = record['image']
    except (KeyError, IndexError, TypeError):
        raise HTTPException(
            status_code=500,
            detail="Can Not Decode Image Data"
        )
    return image, codec


app = FastAPI()


@app.on_event("startup")
async def service_init():
    key_id = '1'
    cluster[1] = await get_cluster()
    collections['service_auth'] = await get_collection(cluster[1], 'service_auth')
    doc_id = f"service_auth:{key_id}"
    result = await collections['service_auth'].lookup_in(doc_id, [SD.get('token')])
    auth_token[1] = result.content_as[str](0)
    collections['user_data'] = await get_collection(cluster[1], 'user_data')
    collections['user_images'] = await get_collection(cluster[1], 'user_images')


@app.get("/api/v1/id/{document}", response_model=Profile)
async def get_by_id(document: str, authorized: bool = Depends(verify_token)):
    if authorized:
        profile = await get_profile(collection=collections['user_data'], collection_name='user_data', document=document)
        return profile


@app.get("/api/v1/nickname/{nickname}", response_model=List[Profile])
async def get_by_nickname(nickname: str, authorized: bool = Depends(verify_token)):
    if authorized:
        records = await query_profiles(cluster=cluster[1], collection_name='user_data', field='nickname', value=nickname)
        return records


@app.get("/api/v1/username/{username}", response_model=List[Profile])
async def get_by_username(username: str, authorized: bool = Depends(verify_token)):
    if authorized:
        records = await query_profiles(cluster=cluster[1], collection_name='user_data', field='user_id', value=username)
        return records


@app.get("/api/v1/picture/record/{document}", response_model=Image)
async def get_image_by_id(document: str, authorized: bool = Depends(verify_token)):
    if authorized:
        image = await get_profile(collection=collections['user_images'], collection_name='user_images', document=document)
        return image


@app.get("/api/v1/picture/raw/{document}")
async def binary_image_by_id(document: str, authorized: bool = Depends(verify_token)):
    if authorized:
        record = await get_profile(collection=collections['user_images'], collection_name='user_images', document=document)
        image, codec = await get_image_data(record)
        content_type = f"image/{codec}"
        response_body = base64.b64decode(bytes(image, "utf-8"))
        return Response(content=response_body, media_type=content_type)
