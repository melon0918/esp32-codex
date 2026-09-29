// UI-6 local visual acceptance: headless installed Edge via Chrome DevTools Protocol.
// No npm dependencies, no production HTTP server, no real bridge. Node 24 required.
"use strict";
const fs=require("node:fs");
const fsp=fs.promises;
const os=require("node:os");
const path=require("node:path");
const {spawn,spawnSync}=require("node:child_process");
const {pathToFileURL}=require("node:url");
const assert=require("node:assert/strict");
const ROOT=path.resolve(__dirname,"..","..");
const REVIEW=path.join(ROOT,"web-panel","review");
const EDGE=process.env.ESP32_TEST_EDGE||"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PYTHON=process.env.ESP32_TEST_PYTHON||path.join(os.tmpdir(),"esp32-ui5-isolated-venv","Scripts","python.exe");
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const checks=[];
function check(name,value){assert.ok(value,name);checks.push(name);}
class Cdp {
 constructor(ws){
  this.ws=ws;this.seq=0;this.pending=new Map();
  ws.addEventListener("message",e=>{
   const m=JSON.parse(e.data.toString());
   if(!m.id||!this.pending.has(m.id))return;
   const q=this.pending.get(m.id);this.pending.delete(m.id);
   if(m.error)q.reject(new Error(JSON.stringify(m.error)));else q.resolve(m.result||{});
  });
 }
 send(method,params={}){
  const id=++this.seq;
  return new Promise((resolve,reject)=>{this.pending.set(id,{resolve,reject});this.ws.send(JSON.stringify({id,method,params}));});
 }
 async js(expression){
  const r=await this.send("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});
  if(r.exceptionDetails)throw new Error("Browser JS: "+JSON.stringify(r.exceptionDetails));
  return r.result.value;
 }
}
async function connect(url){
 const ws=new WebSocket(url);
 await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(new Error("CDP WebSocket timeout")),12000);
  ws.addEventListener("open",()=>{clearTimeout(timer);resolve();},{once:true});
  ws.addEventListener("error",e=>{clearTimeout(timer);reject(e.error||new Error("CDP WebSocket error"));},{once:true});
 });
 return new Cdp(ws);
}
async function until(f,name,tries=100){
 for(let i=0;i<tries;i++){
  try{if(await f())return;}catch(_){}
  await sleep(150);
 }
 throw new Error("Timed out: "+name);
}
async function shot(cdp,name){
 const r=await cdp.send("Page.captureScreenshot",{format:"png",fromSurface:true,captureBeyondViewport:false});
 const data=Buffer.from(r.data,"base64");
 check("PNG "+name,data.length>8000&&data.subarray(1,4).toString()==="PNG");
 await fsp.writeFile(path.join(REVIEW,name),data);
}
(async()=>{
 await fsp.mkdir(REVIEW,{recursive:true});
 const temp=await fsp.mkdtemp(path.join(os.tmpdir(),"esp32-ui6-cdp-"));
 const profile=path.join(temp,"profile"),html=path.join(temp,"operations.html");
 const generator="import sys,pathlib;root=pathlib.Path.cwd();sys.path.insert(0,str(root/'web-panel'/'host'));import host;pathlib.Path(sys.argv[1]).write_text(host.load_operations_page(),encoding='utf-8')";
 const generated=spawnSync(PYTHON,["-B","-c",generator,html],{cwd:ROOT,encoding:"utf8",windowsHide:true});
 if(generated.status!==0)throw new Error("Source-page generation failed: "+generated.stderr);
 let browser,cdp;const requests=[];
 try{
  browser=spawn(EDGE,["--headless=new","--disable-gpu","--no-first-run","--no-default-browser-check","--disable-background-networking","--disable-extensions","--remote-debugging-port=0","--remote-allow-origins=*","--user-data-dir="+profile,"about:blank"],{windowsHide:true,stdio:"ignore"});
  const active=path.join(profile,"DevToolsActivePort");
  await until(async()=>fs.existsSync(active),"Edge debugging port");
  const port=(await fsp.readFile(active,"utf8")).trim().split(/\r?\n/)[0];
  let pages;
  await until(async()=>{
   const r=await fetch("http://127.0.0.1:"+port+"/json/list");
   pages=await r.json();
   return pages.some(x=>x.type==="page");
  },"CDP target");
  cdp=await connect(pages.find(x=>x.type==="page").webSocketDebuggerUrl);
  await cdp.send("Page.enable");await cdp.send("Runtime.enable");await cdp.send("Network.enable");
  cdp.ws.addEventListener("message",e=>{
   const m=JSON.parse(e.data.toString());
   if(m.method==="Network.requestWillBeSent")requests.push(m.params.request.url);
  });
  await cdp.send("Page.addScriptToEvaluateOnNewDocument",{source:await fsp.readFile(path.join(__dirname,"ui6_browser_mock.js"),"utf8")});
  await cdp.send("Emulation.setDeviceMetricsOverride",{width:900,height:820,deviceScaleFactor:1,mobile:false});
  await cdp.send("Page.navigate",{url:pathToFileURL(html).href});
  await until(async()=>await cdp.js("document.readyState==='complete' && document.getElementById('live-source')?.textContent.includes('MOCK')"),"MOCK rendered");
  check("MOCK source displayed",(await cdp.js("document.getElementById('live-source').textContent")).includes("MOCK"));
  check("Operation controls rendered",await cdp.js("Boolean(document.getElementById('ui4-operations'))"));
  await shot(cdp,"ui6-headless-main-900.png");
  await cdp.js("document.querySelector('.rail-link[data-view=\"workspace\"]').click();true");
  check("Live rail goes to real operations",await cdp.js("document.getElementById('crumb-current').textContent==='工作区' && !document.getElementById('prototype-demo').open"));
  await cdp.js("document.querySelector('.rail-link[data-view=\"console\"]').click();true");
  check("Live rail goes to real console",await cdp.js("document.getElementById('crumb-current').textContent==='控制台' && !document.getElementById('prototype-demo').open"));
  await cdp.js("document.querySelector('.rail-link[data-view=\"overview\"]').click();document.getElementById('ui4-operations').scrollIntoView({block:'start'});true");
  await shot(cdp,"ui6-headless-actions-900.png");
  await cdp.js("document.querySelector('[data-ui4-action=\"download\"]').click();true");
  await until(async()=>await cdp.js("!document.getElementById('ui4-confirm').hidden"),"download confirmation");
  check("Confirmation target",(await cdp.js("document.getElementById('ui4-confirm-target').textContent")).includes("MOCK0"));
  check("Confirmation impact",(await cdp.js("document.getElementById('ui4-confirm-impact').textContent")).includes("严格备份"));
  check("Busy control disabled",await cdp.js("document.querySelector('[data-ui4-action=\"download\"]').disabled"));
  await shot(cdp,"ui6-headless-confirm-900.png");
  await cdp.js("document.getElementById('ui4-confirm-reject').click();true");
  await until(async()=>await cdp.js("document.getElementById('ui4-confirm').hidden && !document.querySelector('[data-ui4-action=\"download\"]').disabled"),"cancel completion");
  check("Cancel feedback",(await cdp.js("document.getElementById('ui4-action-status').textContent")).includes("未执行"));
  await cdp.js("document.querySelector('[data-ui4-action=\"download\"]').click();true");
  await until(async()=>await cdp.js("!document.getElementById('ui4-confirm').hidden"),"second confirmation");
  await cdp.js("document.getElementById('ui4-confirm-approve').click();true");
  await until(async()=>await cdp.js("document.getElementById('ui4-confirm').hidden && !document.querySelector('[data-ui4-action=\"download\"]').disabled"),"approval completion");
  check("Approved feedback",(await cdp.js("document.getElementById('ui4-action-status').textContent")).includes("模拟下载完成"));
  await cdp.js("document.getElementById('ui4-repl').value='';document.getElementById('ui4-send').click();true");
  await until(async()=>await cdp.js("document.getElementById('ui4-action-status').classList.contains('ui4-error')"),"error feedback");
  check("Error text",(await cdp.js("document.getElementById('ui4-action-status').textContent")).includes("不能为空"));
  await cdp.js("document.getElementById('ui4-action-status').scrollIntoView({block:'center'});true");
  await shot(cdp,"ui6-headless-error-900.png");
  await cdp.js("document.getElementById('prototype-demo').open=true;document.querySelector('.rail-link[data-view=\"workspace\"]').click();document.getElementById('workspace').scrollIntoView({block:'start'});true");
  check("Settings tab visible",await cdp.js("!document.getElementById('workspace').hidden"));
  await shot(cdp,"ui6-headless-settings-900.png");
  await cdp.js("document.querySelector('.rail-link[data-view=\"console\"]').click();document.getElementById('console').scrollIntoView({block:'start'});true");
  check("Console tab visible",await cdp.js("!document.getElementById('console').hidden"));
  await shot(cdp,"ui6-headless-console-900.png");
  await cdp.js("document.getElementById('prototype-demo').open=false;document.querySelector('.rail-link[data-view=\"overview\"]').click();window.scrollTo(0,0);true");
  for(const [width,height] of [[700,700],[560,720],[467,650]]){
   await cdp.send("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile:false});
   await sleep(350);
   const sizes=await cdp.js("({inner:innerWidth,scroll:document.documentElement.scrollWidth,body:document.body.scrollWidth})");
   console.log("LAYOUT",width,JSON.stringify(sizes));
   check("No horizontal page overflow "+width,sizes.scroll<=sizes.inner && sizes.body<=sizes.inner);
   await shot(cdp,"ui6-headless-narrow-"+width+".png");
  }
  check("No external resource requests",requests.every(u=>u.startsWith("file:")||u.startsWith("data:")||u==="about:blank"));
  const summary={ok:true,checks,requests:requests.length,screenshots:9};
  await fsp.writeFile(path.join(REVIEW,'ui6-headless-result.json'),JSON.stringify(summary,null,2)+'\n');
  console.log(JSON.stringify(summary,null,2));
 }finally{
  try{cdp?.ws.close();}catch(_){}
  if(browser?.pid){
   try{spawnSync('taskkill.exe',['/PID',String(browser.pid),'/T','/F'],{windowsHide:true,stdio:'ignore',timeout:6000});}catch(_){}
  }
  try{await fsp.rm(temp,{recursive:true,force:true,maxRetries:2,retryDelay:100});}catch(_){}
 }
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack||e);process.exit(1);});
