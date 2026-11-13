// Shared by the 3D harvest viewer and the gap-fill story (inlined into a module script).
const PAL = {
  yield: ["#a50026","#d73027","#f46d43","#fdae61","#fee08b","#d9ef8b","#a6d96a","#66bd63","#1a9850","#006837"],
  moisture: ["#ffffd9","#edf8b1","#c7e9b4","#7fcdbb","#41b6c4","#1d91c0","#225ea8","#253494","#081d58"],
  protein: ["#440154","#482878","#3e4989","#31688e","#26828e","#1f9e89","#35b779","#6ece58","#b5de2b","#fde725"],
  oil: ["#ffffe5","#fff7bc","#fee391","#fec44f","#fe9929","#ec7014","#cc4c02","#993404","#662506"],
  carbohydrate: ["#00204d","#00336f","#39486b","#575c6e","#707173","#8a8779","#a69d75","#c4b56b","#e4cf5b","#ffea46"],
  starch: ["#fff7fb","#ece2f0","#d0d1e6","#a6bddb","#67a9cf","#3690c0","#02818a","#016c59","#014636"],
  ndvi: ["#a50026","#f46d43","#fee08b","#d9ef8b","#66bd63","#1a9850","#006837"],
  sigma: ["#ffffcc","#fed976","#fd8d3c","#e31a1c","#800026"]
};
function hex(c){return [parseInt(c.slice(1,3),16),parseInt(c.slice(3,5),16),parseInt(c.slice(5,7),16)];}
function rgb(pal, f){
  const s = PAL[pal]; f = Math.min(1, Math.max(0, f)); const p = f*(s.length-1), i = Math.min(s.length-2, Math.floor(p)), w = p-i, a = hex(s[i]), b = hex(s[i+1]);
  return [Math.round(a[0]+(b[0]-a[0])*w), Math.round(a[1]+(b[1]-a[1])*w), Math.round(a[2]+(b[2]-a[2])*w)];
}
function colour(pal, f){ const c = rgb(pal, f); return `rgb(${c[0]},${c[1]},${c[2]})`; }
async function unpack(b64){
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const s = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(s).text());
}

// --- terrain heights (metres above the lowest point), bilinear -------------------------
function makeTerrain(TR){
  const T = {W:(TR.nx-1)*TR.step, H:(TR.ny-1)*TR.step, exag:3, TR};
  T.raw = (x, y) => {
    const fx = Math.min(TR.nx-1.001, Math.max(0, x/TR.step)), fy = Math.min(TR.ny-1.001, Math.max(0, y/TR.step));
    const i = Math.floor(fx), j = Math.floor(fy), u = fx-i, v = fy-j, n = TR.nx, h = TR.h;
    return (h[j*n+i]*(1-u)+h[j*n+i+1]*u)*(1-v) + (h[(j+1)*n+i]*(1-u)+h[(j+1)*n+i+1]*u)*v;
  };
  T.at = (x, y) => T.raw(x, y) * T.exag;
  return T;
}
function makeHillshade(TR){ // canvas TR.nx x TR.ny, north up
  const base = document.createElement("canvas"); base.width = TR.nx; base.height = TR.ny;
  const b = base.getContext("2d"), img = b.createImageData(TR.nx, TR.ny), n = TR.nx, h = TR.h, s = TR.step;
  for (let j=0;j<TR.ny;j++) for (let i=0;i<n;i++){
    const i0=Math.max(0,i-1), i1=Math.min(n-1,i+1), j0=Math.max(0,j-1), j1=Math.min(TR.ny-1,j+1);
    const gx=(h[j*n+i1]-h[j*n+i0])/((i1-i0)*s), gy=(h[j1*n+i]-h[j0*n+i])/((j1-j0)*s);
    const nl=Math.hypot(gx,gy,1), l=(-gx*-0.5+-gy*0.5+1*0.7)/(nl*Math.hypot(0.5,0.5,0.7));
    const v = 95+125*Math.max(0,l), row = TR.ny-1-j, k=(row*n+i)*4;
    img.data[k]=Math.round(v*0.93); img.data[k+1]=Math.round(v*0.97); img.data[k+2]=Math.round(v*0.82); img.data[k+3]=255;
  }
  b.putImageData(img,0,0);
  return base;
}

// --- three.js scene ---------------------------------------------------------------------
function initScene(container){
  const renderer = new THREE.WebGLRenderer({antialias:true});
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  scene.background = new THREE.Color(dark ? 0x141a20 : 0xdfe7ee);
  const camera = new THREE.PerspectiveCamera(45, 1, 1, 60000);
  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.maxPolarAngle = Math.PI*0.49;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 0.75));
  const sun = new THREE.DirectionalLight(0xffffff, 0.75); sun.position.set(-1500, 2500, 1500); scene.add(sun);
  const S = {pw: 0};
  // the control panel covers the left edge: keep the scene in the free part of the window
  function resize(){
    const w = innerWidth, h = innerHeight, panel = document.getElementById("panel");
    S.pw = panel && w > 560 ? panel.offsetWidth + 24 : 0;
    renderer.setSize(w, h); camera.aspect = w/h;
    if (S.pw) camera.setViewOffset(w, h, -S.pw/2, 0, w, h); else camera.clearViewOffset();
    camera.updateProjectionMatrix();
  }
  addEventListener("resize", resize); resize();
  // animated camera moves
  const tw = {on:false, t:0, dur:0.9, p0:new THREE.Vector3(), p1:new THREE.Vector3(), g0:new THREE.Vector3(), g1:new THREE.Vector3()};
  function flyTo(target, pos){
    tw.p0.copy(camera.position); tw.g0.copy(controls.target); tw.p1.copy(pos); tw.g1.copy(target); tw.t = 0; tw.on = true;
  }
  function stepCamera(dt){
    if (!tw.on) return;
    tw.t = Math.min(1, tw.t + dt/tw.dur); const e = tw.t<.5 ? 2*tw.t*tw.t : 1-Math.pow(-2*tw.t+2,2)/2;
    camera.position.lerpVectors(tw.p0, tw.p1, e); controls.target.lerpVectors(tw.g0, tw.g1, e);
    if (tw.t >= 1) tw.on = false;
  }
  // camera that fits the box [minX,maxX] x [minZ,maxZ] seen obliquely from the south-west
  function fitBox(minX, maxX, minZ, maxZ, flyIt=true){
    const cx=(minX+maxX)/2, cz=(minZ+maxZ)/2, ex=maxX-minX, ez=maxZ-minZ;
    const fv = camera.fov*Math.PI/180, aspect = Math.max(0.3, (innerWidth - S.pw)/innerHeight), fh = 2*Math.atan(Math.tan(fv/2)*aspect);
    const dist = 1.25*Math.max(ex/2/Math.tan(fh/2), (ez*0.95+ex*0.1)/2/Math.tan(fv/2));
    const d = new THREE.Vector3(-0.22, 0.82, 0.58).normalize();
    const target = new THREE.Vector3(cx, 0, cz), pos = target.clone().add(d.multiplyScalar(dist));
    if (flyIt) flyTo(target, pos); else { controls.target.copy(target); camera.position.copy(pos); controls.update(); }
  }
  Object.assign(S, {renderer, scene, camera, controls, flyTo, stepCamera, fitBox, resize});
  return S;
}

// --- combine model --------------------------------------------------------------------
function makeCombine(width){
  const g = new THREE.Group(), body = new THREE.Group(); g.add(body);
  const green = new THREE.MeshLambertMaterial({color:0x2f7d32}), dark = new THREE.MeshLambertMaterial({color:0x263238}), glass = new THREE.MeshLambertMaterial({color:0x90caf9});
  const box = (w,h,l,m,x,y,z) => { const o = new THREE.Mesh(new THREE.BoxGeometry(w,h,l), m); o.position.set(x,y,z); body.add(o); return o; };
  box(3.2,2.6,7.0,green,0,2.2,5.0); box(2.2,1.8,2.0,glass,0,4.3,2.6); box(2.4,0.3,2.2,green,0,5.3,2.6); box(0.35,0.35,5.5,dark,-2.2,4.2,6.0);
  for (const [x,z,r] of [[-1.7,2.6,0.95],[1.7,2.6,0.95],[-1.5,7.6,0.6],[1.5,7.6,0.6]]){
    const w = new THREE.Mesh(new THREE.CylinderGeometry(r,r,0.6,20), dark); w.rotation.z = Math.PI/2; w.position.set(x, r, z); body.add(w);
  }
  const header = new THREE.Group(); g.add(header);
  const hm = new THREE.MeshLambertMaterial({color:0xf2c94c});
  const bar = new THREE.Mesh(new THREE.BoxGeometry(width,0.7,1.8), hm); bar.position.set(0,0,-0.2); header.add(bar);
  const reel = new THREE.Mesh(new THREE.CylinderGeometry(0.45,0.45,width*0.97,16), new THREE.MeshLambertMaterial({color:0xc0392b}));
  reel.rotation.z = Math.PI/2; reel.position.set(0,1.0,-0.6); header.add(reel);
  const feeder = new THREE.Mesh(new THREE.BoxGeometry(1.4,0.8,2.2), green); feeder.position.set(0,0.6,1.2); header.add(feeder);
  g.userData = {header, hm, hY:0.4};
  return g;
}
