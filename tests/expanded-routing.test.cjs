const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const zlib = require('node:zlib');
const { createHash } = require('node:crypto');
const context = vm.createContext({ TextDecoder, performance, window: {} });
vm.runInContext(fs.readFileSync('sf_flat_routes/web/engine.js', 'utf8') + '\nthis.Graph=Graph;this.Bundle=Bundle;', context);
const data = JSON.parse(fs.readFileSync('site/index.html', 'utf8').match(/window.DATA = (.*);<\/script>/)[1]);
const graph = new context.Graph(new context.Bundle(new Uint8Array(zlib.gunzipSync(fs.readFileSync('site/' + data.bundle_url))), data.manifest), data.meta);
const weights = { alpha: 0, beta: 0, gamma: 0, penalties: [0,0,0,0,0], use_class_multiplier: false };
test('committed website uses the current source and valid content-hashed assets',()=>{
  const html=fs.readFileSync('site/index.html','utf8');
  const js=html.match(/<script src="(app-[^"]+\.js)">/)[1];
  const css=html.match(/<link rel="stylesheet" href="(app-[^"]+\.css)">/)[1];
  const source=fs.readFileSync('sf_flat_routes/web/engine.js','utf8')+'\n'+fs.readFileSync('sf_flat_routes/web/simple.js','utf8');
  assert.equal(fs.readFileSync('site/'+js,'utf8'),source);
  assert.equal(fs.readFileSync('site/'+css,'utf8'),fs.readFileSync('sf_flat_routes/web/simple.css','utf8'));
  for(const name of [js,css]) assert.equal(name.split('-')[1].split('.')[0],createHash('sha1').update(fs.readFileSync('site/'+name)).digest('hex').slice(0,10));
  for(const match of html.matchAll(/(?:src|href)="([^"#]+)"/g)) if(!match[1].includes('://')) assert.ok(fs.existsSync('site/'+match[1]),match[1]);
  assert.ok(fs.existsSync('site/'+data.bundle_url));
  assert.ok(fs.existsSync('site/'+data.hillshade.url));
});
function nearest(lon, lat, mode) {
  let best=-1, distance=Infinity;
  for(let i=0;i<graph.n;i++) {
    if(!(graph.nodeFlags[i]&graph.modeBit(mode))) continue;
    const d=(graph.nodeLon(i)-lon)**2+(graph.nodeLat(i)-lat)**2;
    if(d<distance) {distance=d;best=i;}
  }
  return best;
}
function search(g, from, to, mode, cap, maxLabels=4e6) {
  const result=g.pareto(from,to,mode,{eps:1,epsNode:1,dCap:cap,maxLabels});
  while(!result.step(30)) {}
  return result;
}
function tinyGraph(arcs, n) {
  const g=Object.create(context.Graph.prototype);
  Object.assign(g,{n,m:arcs.length,DM:1,CM:1});
  const sorted=arcs.toSorted((a,b)=>a[0]-b[0]);
  g.indptr=new Int32Array(n+1);
  for(const [u] of sorted) g.indptr[u+1]++;
  for(let i=0;i<n;i++) g.indptr[i+1]+=g.indptr[i];
  g.head=Int32Array.from(sorted.map(a=>a[1]));
  g.arcLen=Int32Array.from(sorted.map(a=>a[2]));
  g.arcGain=Int32Array.from(sorted.map(a=>a[3]));
  g.arcFlags=Uint8Array.from(sorted.map(()=>3));
  return g;
}
test('400% longer permits a five-times-longer optimum that the old alpha cutoff misses',()=>{
  const g=tinyGraph([[0,1,1000,10],[0,2,2000,0],[2,1,3000,0],[0,3,2000,0],[3,1,3001,0]],4);
  const capped=search(g,0,1,'walk',5000);
  assert.equal(capped.truncated,false);
  assert.equal(capped.solutions.at(-1).length,5000);
  assert.equal(capped.solutions.at(-1).gain,0);
  assert.ok(1000+200*10 < 5000, 'old weighted objective prefers the hill');
  assert.ok(search(g,0,1,'walk',4999).solutions.every(r=>r.gain===10));
});
test('integer-exact Pareto search agrees with exhaustive simple paths on 25 independent graphs',()=>{
  let seed=731;
  const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed;};
  for(let sample=0;sample<25;sample++) {
    const arcs=[];
    for(let u=0;u<7;u++) for(let v=0;v<7;v++) if(u!==v && (v===u+1 || random()%4===0)) arcs.push([u,v,1+random()%20,random()%8]);
    const g=tinyGraph(arcs,7), cap=60, paths=[];
    function visit(u,d,c,seen) {
      if(u===6) {paths.push([d,c]);return;}
      for(const [a,b,len,gain] of arcs) if(a===u && !seen.has(b) && d+len<=cap) visit(b,d+len,c+gain,new Set([...seen,b]));
    }
    visit(0,0,0,new Set([0]));
    const frontier=paths.filter(([d,c])=>!paths.some(([x,y])=>x<=d && y<=c && (x<d || y<c)));
    const expected=[...new Set(frontier.map(p=>p.join(',')))].sort();
    const result=search(g,0,6,'walk',cap);
    assert.equal(result.truncated,false);
    assert.deepEqual(Array.from(result.solutions,r=>[r.length,r.gain].join(',')).sort(),expected);
  }
});
test('real SF graph: all budgets, walking/biking, and distant/nearby trips stay bounded and monotonic',()=>{
  const trips=[data.default.map(p=>[p.lon,p.lat]),[[-122.407,37.784],[-122.417,37.795]],[[-122.383,37.730],[-122.484,37.770]],[[-122.438,37.802],[-122.493,37.755]]];
  for(const mode of ['walk','bike']) for(const [start,end] of trips) {
    const from=nearest(...start,mode),to=nearest(...end,mode);
    const shortest=graph.route(from,to,mode,weights);
    const base=shortest.arcs.reduce((sum,a)=>sum+graph.arcLen[a],0);
    let lastGain=Infinity;
    for(const extra of [0,25,50,100,200,300,400]) {
      const cap=base*(1+extra/100),result=search(graph,from,to,mode,cap);
      assert.equal(result.truncated,false,`${mode} ${start} +${extra}%`);
      assert.ok(result.solutions.length>0);
      for(let i=0;i<result.solutions.length;i++) {
        const r=result.solutions[i];
        assert.ok(r.length*graph.DM<=cap+1e-6);
        assert.ok(r.arcs.every(a=>(graph.arcFlags[a]&graph.modeBit(mode))!==0));
        if(i) {assert.ok(r.length>=result.solutions[i-1].length);assert.ok(r.gain<result.solutions[i-1].gain);}
      }
      const gain=result.solutions.at(-1).gain;
      assert.ok(gain<=lastGain);
      lastGain=gain;
    }
    const best=search(graph,from,to,mode,base*5).solutions.at(-1);
    const original=graph.route(from,to,mode,{...weights,alpha:200});
    const originalStats=graph.summarise(original.arcs);
    if(originalStats.distance_m<=base*5/graph.DM) assert.ok(best.gain<=originalStats.elev_gain_m+1e-6);
    console.log(`${mode} ${start}: ${ (base/graph.DM/1609.344).toFixed(2)} mi shortest, ${(best.length/1609.344).toFixed(2)} mi least climbing, ${(best.gain*3.28084).toFixed(1)} ft ascent`);
  }
});
test('search size limit explicitly reports truncation',()=>{
  const result=search(graph,nearest(...[data.default[0].lon,data.default[0].lat],'walk'),nearest(...[data.default[1].lon,data.default[1].lat],'walk'),'walk',1e9,10);
  assert.equal(result.truncated,true);
});
function appHarness() {
  const elements=new Map(),timers=[];
  const element=(id)=>{
    if(!elements.has(id)) elements.set(id,{value:'',textContent:'',children:[],dataset:{},style:{},hidden:false,offsetHeight:0,
      listeners:{},classList:{add(){},remove(){},toggle(){}},setAttribute(){},querySelectorAll(){return [];},
      addEventListener(name,fn){this.listeners[name]=fn;}});
    return elements.get(id);
  };
  const window={DATA:data,addEventListener(){}};
  const sandbox=vm.createContext({window,document:{getElementById:element},L:{Layer:{extend(){return {};}}},
    performance,TextDecoder,localStorage:{getItem(){return null;}},location:{hash:''},
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},console});
  vm.runInContext(fs.readFileSync('sf_flat_routes/web/simple.js','utf8').replace('App.start().catch','Promise.resolve().catch'),sandbox);
  const app=window.App;
  const from=nearest(data.default[0].lon,data.default[0].lat,'walk'),to=nearest(data.default[1].lon,data.default[1].lat,'walk');
  app.graph=graph;
  app.state.from={...data.default[0],node:from};app.state.to={...data.default[1],node:to};
  app.member=arcs=>({arcs,stats:graph.summarise(arcs)});
  for(const name of ['drawFamily','show','fit','drawMarkers','scanEnd','writeHash']) app[name]=()=>{};
  return {app,element,sandbox,flush(){while(timers.length) timers.shift()();}};
}
test('actual app routing wiring uses five times physical distance and preserves endpoints after thinning',()=>{
  const h=appHarness();
  h.app.recompute(false);h.flush();
  const family=h.app.family;
  assert.equal(family.partial,false);assert.equal(family.truncated,false);
  assert.ok(family.unique.length<=30);
  assert.equal(family.unique.at(-1).stats.elev_gain_m.toFixed(2),h.app._search.solutions.at(-1).gain.toFixed(2));
  assert.ok(family.unique.every(r=>r.stats.distance_m<=family.shortest.stats.distance_m*5+1e-6));
  assert.match(h.element('status').textContent,/Least climbing found within \+400%/);
  h.app.state.mode='bike';h.app.state.calm=true;assert.equal(h.app.calm(),false);
  h.app.state.loop=true;assert.equal(h.app.calm(),true);
});
test('distance allowance round-trips in share links, and old/invalid links keep the default',()=>{
  const h=appHarness();h.app.state.expansion=200;
  const token=h.app.token();assert.equal(token.split('~')[9],'200');
  h.app.pointAt=(lon,lat,label)=>({lon,lat,label});
  h.app.setPoint=(name,p)=>{h.app.state[name]=p;};
  for(const [hash,want] of [[token,200],[token.split('~').slice(0,9).join('~'),400],[token.replace(/200$/,'900'),400]]) {
    h.app.state.expansion=400;h.sandbox.location.hash='#'+hash;
    assert.equal(h.app.readHash(),true);assert.equal(h.app.state.expansion,want);
  }
});
test('distance control change really recomputes the selected allowance',()=>{
  const h=appHarness();h.app.buildUI();let recomputed=false;
  h.app.recompute=()=>{recomputed=true;};
  h.element('expansion').value='100';h.element('expansion').listeners.change();
  assert.equal(h.app.state.expansion,100);assert.equal(recomputed,true);
  assert.match(h.element('expansionhint').textContent,/up to 2 miles/);
});
test('KML keeps every coordinate of the selected SF route with no simplification',()=>{
  const h=appHarness();h.app.recompute(false);h.flush();
  const bytes=new Uint8Array(zlib.gunzipSync(fs.readFileSync('site/'+data.bundle_url)));
  vm.runInContext('this.Geometry=Geometry;',context);
  const geometry=new context.Geometry(new context.Bundle(bytes,data.manifest),data.meta);
  h.app.shown=h.app.family.unique.at(-1);
  h.app.shown.latlngs=graph.geometry(h.app.shown.arcs,geometry);
  const kml=h.app.kml();
  const decoded=kml.match(/<coordinates>([\s\S]*?)<\/coordinates>/)[1].trim().split(/\s+/).map(line=>line.split(',').map(Number));
  assert.equal(decoded.length,h.app.shown.latlngs.length);
  for(let i=0;i<decoded.length;i++) assert.deepEqual(decoded[i],[h.app.shown.latlngs[i][1],h.app.shown.latlngs[i][0],0]);
  console.log(`Exact Google map sample: ${decoded.length} unchanged vertices`);
});
test('Google map copy validates the destination and copies its actual viewer link',async()=>{
  const h=appHarness();let copied;
  h.sandbox.navigator={clipboard:{writeText:async(text)=>{copied=text;}}};
  h.element('googleurl').value='https://www.google.com/maps/d/u/0/edit?mid=example-map-123&ll=37.7';
  h.app.copyGoogleMap();await Promise.resolve();
  assert.equal(copied,'https://www.google.com/maps/d/viewer?mid=example-map-123');
  copied=undefined;h.element('googleurl').value='https://evil.example/maps/d/?mid=bad';h.app.copyGoogleMap();
  assert.equal(copied,undefined);
  assert.match(h.element('googlestatus').textContent,/Paste the Google My Maps link/);
});
