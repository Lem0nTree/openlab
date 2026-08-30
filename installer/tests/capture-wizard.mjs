// Minimal Chrome DevTools visual-QA helper; it writes screenshots only and has
// no production/browser runtime dependency.
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const [credentialsPath, outputDir, chrome] = process.argv.slice(2);
const credentials = JSON.parse(await readFile(credentialsPath, "utf8"));
await mkdir(outputDir, { recursive: true });
const profile = path.join(tmpdir(), `openlab-wizard-visual-${process.pid}`);
const port = 9333 + (process.pid % 300);
const processHandle = spawn(chrome, [`--headless=new`, `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, "--no-first-run", "--disable-gpu", "--window-size=1440,1050", "about:blank"],
  { stdio: "ignore" });
try {
  let target;
  for (let attempt=0; attempt<50; attempt++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(item => item.type === "page"); if(target)break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!target) throw new Error("Chrome debugging target unavailable");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject) => { socket.onopen=resolve;socket.onerror=reject; });
  let sequence=0;const waiting=new Map();
  socket.onmessage = event => { const message=JSON.parse(event.data);if(message.id&&waiting.has(message.id)){const {resolve,reject}=waiting.get(message.id);waiting.delete(message.id);message.error?reject(new Error(message.error.message)):resolve(message.result);} };
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;waiting.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=expression=>send("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true});
  const waitFor=async expression=>{for(let attempt=0;attempt<300;attempt++){if((await evaluate(expression)).result.value)return;await new Promise(resolve=>setTimeout(resolve,100));}const state=(await evaluate("({url:location.href,text:document.body?.innerText?.slice(0,500)})")).result.value;throw new Error(`Timed out: ${expression}; state=${JSON.stringify(state)}`);};
  await send("Page.enable");
  await send("Page.navigate",{url:`${credentials.url}/login`});
  await waitFor("document.readyState === 'complete' && !!document.querySelector('form')");
  const formValues=JSON.stringify({email:credentials.email,password:credentials.password});
  await evaluate(`(()=>{const v=${formValues};for(const [name,value] of Object.entries(v)){const e=document.querySelector('[name="'+name+'"]');const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}));}document.querySelector('form').requestSubmit();return true})()`);
  await waitFor("location.pathname !== '/login'");
  await send("Page.navigate",{url:`${credentials.url}/onboarding`});
  await waitFor("document.readyState === 'complete' && document.body.innerText.includes('Let’s get your lab ready.')");
  for(const viewport of [{name:"desktop",width:1440,height:1050},{name:"mobile",width:390,height:844}]){
    await send("Emulation.setDeviceMetricsOverride",{width:viewport.width,height:viewport.height,deviceScaleFactor:1,mobile:viewport.name==="mobile"});
    await new Promise(resolve=>setTimeout(resolve,200));
    const shot=await send("Page.captureScreenshot",{format:"png",captureBeyondViewport:false,fromSurface:true});
    await writeFile(path.join(outputDir,`onboarding-${viewport.name}.png`),Buffer.from(shot.data,"base64"));
    const audit=(await evaluate(`({title:document.title,heading:document.querySelector('h1')?.innerText,overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,buttons:[...document.querySelectorAll('button')].filter(x=>!x.disabled).map(x=>x.innerText).filter(Boolean),errors:[...document.querySelectorAll('[role="alert"],.error')].map(x=>x.innerText)})`)).result.value;
    if(audit.overflow||audit.errors.length||audit.heading!=="Let’s get your lab ready.")throw new Error(`Visual DOM audit failed: ${JSON.stringify(audit)}`);
    console.log(JSON.stringify({viewport,...audit}));
  }
  socket.close();
} finally {
  processHandle.kill();
}
