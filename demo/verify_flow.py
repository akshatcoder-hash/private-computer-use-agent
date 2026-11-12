import json, base64, urllib.request, sys, glob, os
from playwright.sync_api import sync_playwright
# Checks the bank flow in headless Chromium and asks Atom to hit each target.
# usage: python demo/verify_flow.py   (needs demo/serve.py on BANK_URL and Atom on 127.0.0.1:8760)
import tempfile
BASE=os.environ.get("BANK_URL","http://127.0.0.1:8770/"); SH=os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="bank-shots-")+"/"
def post(u,d): return json.load(urllib.request.urlopen(urllib.request.Request(u,json.dumps(d).encode(),{"Content-Type":"application/json"})))
def atom(png,instr):
    r=post("http://127.0.0.1:8760/ground",{"instruction":instr,"image_base64":base64.b64encode(png).decode()})
    return r["result"]["pixel"]
exe=glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-1254/*/chrome-headless-shell"))+glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-1254/*/*/chrome-headless-shell"))
urllib.request.urlopen(urllib.request.Request(BASE+"reset",b"",method="POST"))
res=[]
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe[0])
    c=b.new_context(viewport={"width":1400,"height":1050},accept_downloads=True); pg=c.new_page()
    def check(instr,loc,shot):
        png=pg.screenshot(path=SH+shot+".png"); px=atom(png,instr)
        bb=loc.bounding_box(); hit=bb["x"]<=px["x"]<=bb["x"]+bb["width"] and bb["y"]<=px["y"]<=bb["y"]+bb["height"]
        res.append((instr,px,{k:round(v) for k,v in bb.items()},"HIT" if hit else "MISS")); print(res[-1],flush=True)
    pg.goto(BASE); check("click the Statements link",pg.get_by_role("link",name="Statements",exact=True),"1-accounts")
    pg.get_by_role("link",name="Statements",exact=True).click(); pg.wait_for_load_state()
    check("click the View October 2026 button",pg.get_by_role("link",name="View October 2026"),"2-statements")
    pg.get_by_role("link",name="View October 2026").click(); pg.wait_for_load_state()
    check("click the Download PDF button",pg.get_by_role("button",name="Download PDF"),"3-statement-october")
    with pg.expect_download() as d: pg.get_by_role("button",name="Download PDF").click()
    print("download",d.value.suggested_filename); d.value.save_as("/tmp/dl-test.pdf"); pg.wait_for_timeout(300)
    print("banner:",pg.locator("#slot").inner_text()); pg.screenshot(path=SH+"4-statement-downloaded.png")
    pg.goto(BASE+"index.html"); check("click the Transfers link",pg.get_by_role("link",name="Transfers",exact=True),"5-accounts-b")
    pg.get_by_role("link",name="Transfers",exact=True).click(); pg.wait_for_load_state()
    check("click the To field",pg.get_by_label("To",exact=True),"6-transfers")
    pg.get_by_label("To",exact=True).fill("Jordan Reyes"); pg.get_by_label("Amount").fill("250.00")
    check("click the Review transfer button",pg.get_by_role("button",name="Review transfer"),"7-transfers-filled")
    pg.get_by_role("button",name="Review transfer").click(); pg.wait_for_load_state(); pg.screenshot(path=SH+"8-review.png")
    pg.get_by_role("button",name="Confirm transfer").click(); pg.wait_for_timeout(300); pg.screenshot(path=SH+"9-confirmed.png")
    pg.goto(BASE+"cards.html"); pg.screenshot(path=SH+"10-cards.png"); pg.wait_for_timeout(300)
    print("scrollH", pg.evaluate("document.documentElement.scrollHeight"))
    for u in ["statements.html","statement-october-2026.html","transfers.html"]:
        pg.goto(BASE+u); print(u,"scrollH",pg.evaluate("document.documentElement.scrollHeight"))
    b.close()
print([e["name"]+":"+e["detail"] for e in json.load(urllib.request.urlopen(BASE+"events"))])
print("MISSES:",[r for r in res if r[3]=="MISS"])
