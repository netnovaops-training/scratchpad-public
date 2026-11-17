import json, sys, urllib.request
pw = open(sys.argv[1], encoding="utf-8").read().strip()
q = {"query": 'mutation($u:String!,$p:String!){authentication{login(username:$u,password:$p,strategy:"local"){responseResult{succeeded}}}}',
     "variables": {"u": "robin-test@example.com", "p": pw}}
req = urllib.request.Request("http://127.0.0.1:13000/graphql", data=json.dumps(q).encode(),
                             headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req, timeout=30))
print("admin sign-in:", r["data"]["authentication"]["login"]["responseResult"]["succeeded"])
