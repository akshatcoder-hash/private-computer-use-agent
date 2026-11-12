
function ev(name, detail, extra){
  try{ fetch('/event',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({name:name,detail:detail},extra||{})),keepalive:true}); }catch(e){}
}
ev('page_view', location.pathname.replace(/^\//,'') || 'index.html');
