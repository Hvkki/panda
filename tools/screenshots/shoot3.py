import asyncio, json, os, subprocess, sys, time
import requests
from playwright.async_api import async_playwright

URL="https://ufvskhagspdjslomzwjh.supabase.co"; ANON="sb_publishable_fZpIgjFiiMbLIyehPMxARQ_zCcjBZRT"
SECRET=os.environ["SB_SECRET"]; TID="3793df2a-2133-49e9-835e-1d9aac1c43f6"
PW="Shoot-"+os.urandom(6).hex(); CHROME="/inner_container/opt/playwright/chromium-1232/chrome-linux64/chrome"
SK="/projects/sandbox/kirocrew-relay/worker/skills/panda-browser"; HOME="/projects/sandbox/.pwhome/home3"
TOPIC="pbr-shot-"+os.urandom(6).hex(); SITE="https://panda-app-gamma.vercel.app"; OUT="/projects/sandbox/.kiro/artifacts/screenshots/"
A={"apikey":SECRET,"Authorization":f"Bearer {SECRET}","Content-Type":"application/json"}
def admin(m,p,b=None,prefer=""):
    h=dict(A,**({"Prefer":prefer} if prefer else {}))
    r=requests.request(m,f"{URL}/rest/v1/{p}",headers=h,json=b); r.raise_for_status(); return r.json() if r.text else None

requests.put(f"{URL}/auth/v1/admin/users/{TID}",headers=A,json={"password":PW})
# clear old tester sessions + fake a running one so the app shows "online"
admin("PATCH","worker_sessions?owner=eq."+TID,{"status":"stopped"})
now=time.strftime("%Y-%m-%dT%H:%M:%S+00:00",time.gmtime())
admin("POST","worker_sessions",{"owner":TID,"status":"running","started_at":now,"heartbeat_at":now})
admin("POST","user_settings",{"owner":TID,"settings":{"browserTopic":TOPIC,"showThinking":False}},prefer="resolution=merge-duplicates")
chat=admin("POST","chats",{"owner":TID,"title":"open wikipedia red panda","slot_id":"s"},prefer="return=representation")[0]
mk=lambda **k: admin("POST","messages",{"chat_id":chat["id"],"owner":TID,**k},prefer="return=representation")[0]
u=mk(role="user",content="open the red panda wikipedia page",status="done")
mk(role="assistant",reply_to=u["id"],status="done",content="I opened Apple's website and went to the AirPods page \u2014 it's live below. Want me to compare the models or check the price?",
   meta={"model":"claude-sonnet-5.5","secs":12,"tools":["Running: python3 ~/.kiro/crew/skills/panda-browser/pb.py open apple.com","Running: python3 ~/.kiro/crew/skills/panda-browser/pb.py open apple.com/airpods"]})

os.makedirs(f"{HOME}/.panda",exist_ok=True)
json.dump({"supabase_url":URL,"anon":ANON,"topic":TOPIC},open(f"{HOME}/.panda/browser.json","w"))
env=dict(os.environ,HOME=HOME,PANDA_CHROMIUM=CHROME,PANDA_BROWSER_PORT="5483",PANDA_OUTPUTS=f"{HOME}/outputs")
eng=subprocess.Popen([sys.executable,f"{SK}/engine.py"],env=env,stdout=open(f"{HOME}/e.log","w"),stderr=subprocess.STDOUT)
pb=lambda *a: subprocess.run([sys.executable,f"{SK}/pb.py",*a],env=env,capture_output=True,text=True,timeout=120)

async def main():
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if pb("tabs").returncode==0: break
        print("open:", pb("open","https://en.wikipedia.org/wiki/Red_panda").stdout[:60]); await asyncio.sleep(1); print("status:", pb("tabs").stdout[:120])
        async with async_playwright() as p:
            b=await p.chromium.launch(executable_path=CHROME,args=["--no-sandbox","--disable-crash-reporter","--disable-gpu"])
            for name,w,h in (("tablet",820,1180),("phone",400,860)):
                page=await (await b.new_context(viewport={"width":w,"height":h},device_scale_factor=1)).new_page()
                await page.goto(f"{SITE}/?c={chat['id']}")
                await page.wait_for_selector("input[type=email]",timeout=25000)
                await asyncio.sleep(5)  # let the one-time service-worker reload settle
                for _ in range(5):
                    await page.fill("input[type=email]","tester@kirocrew.local")
                    await page.fill("input[type=password]",PW)
                    if await page.input_value("input[type=email]")=="tester@kirocrew.local": break
                    await asyncio.sleep(1)
                await page.click("button.btn.primary")
                try:
                    await page.wait_for_selector(".bi-stage img",timeout=35000)
                    await asyncio.sleep(2)
                except Exception as e:
                    print(name,"no inline frame:",str(e)[:80])
                await page.screenshot(path=OUT+f"v4-chat-{name}.png",full_page=(name=="tablet"))
            await b.close()
        print("shots done")
    finally:
        eng.terminate()
        try: eng.wait(8)
        except Exception: eng.kill()
        admin("DELETE","chats?id=eq."+chat["id"])
        admin("PATCH","worker_sessions?owner=eq."+TID,{"status":"stopped"})
        requests.put(f"{URL}/auth/v1/admin/users/{TID}",headers=A,json={"password":os.urandom(20).hex()})
asyncio.run(main())
