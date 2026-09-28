/* Browser-only UI-6 mock. No broker connection or hardware access. */
window.__ui6Calls = [];
window.__ui6Pending = null;
window.__ui6State = {
  simulated:true,
  workspace:{path:"C:\\MOCK\\four-legged-robot",profile:"generic",label:"UI6 模拟工作区",entry:"/main.py"},
  policy:{name:"confirm-write",source:"default"},
  status:{connected:true,port:"MOCK0",firmware:"MicroPython MOCK",busy:false},
  ports:[{device:"MOCK0"}],
  console:{text:"[MOCK] 启动完成\n[MOCK] 模拟控制台输出",cursor:56,dropped:false}
};
window.pywebview={api:{
  get_snapshot:async()=>window.__ui6State,
  discover_workspaces:async()=>({workspaces:[{workspacePath:"C:\\MOCK\\four-legged-robot",profile:"generic",entry:"/main.py"}],truncated:false}),
  perform:async(action,payload)=>{
    window.__ui6Calls.push({action,payload});
    if(action==="download"||action==="download_run"){
      return new Promise(resolve=>{
        window.__ui6Pending={action,resolve};
        window.__ui4ShowConfirmation({title:"批准本次操作："+action,target:"MOCK0 /main.py",impact:"仅模拟严格备份、下载与校验"});
      });
    }
    if(action==="repl"&&!payload.line.trim())return {ok:false,error:"演示错误：命令不能为空",snapshot:window.__ui6State};
    return {ok:true,message:"MOCK 操作完成："+action,snapshot:window.__ui6State};
  },
  approve_current_operation:async()=>{
    const p=window.__ui6Pending;if(!p)return {ok:false};
    window.__ui6Pending=null;p.resolve({ok:true,message:"模拟下载完成",snapshot:window.__ui6State});
    return {ok:true};
  },
  reject_current_operation:async()=>{
    const p=window.__ui6Pending;if(!p)return {ok:false};
    window.__ui6Pending=null;p.resolve({ok:false,error:"已取消；未执行。",snapshot:window.__ui6State});
    return {ok:true};
  }
}};
window.addEventListener("DOMContentLoaded",()=>window.dispatchEvent(new Event("pywebviewready")),{once:true});
