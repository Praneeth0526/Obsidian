import json
import urllib.request

url = "http://localhost:9200/hpe-search-docs/_search?search_pipeline=hybrid-search-pipeline"
headers = {"Content-Type": "application/json"}
body = {
  "size": 5,
  "query": {
    "hybrid": {
      "queries": [
        {
          "match": {
            "chunk_text": "devops"
          }
        },
        {
          "match_all": {}
        }
      ]
    }
  }
}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
try:
    with urllib.request.urlopen(req) as f:
        res = json.loads(f.read().decode('utf-8'))
        print(json.dumps(res, indent=2))
except Exception as e:
    print(e)
    if hasattr(e, 'read'):
        print(e.read().decode())
