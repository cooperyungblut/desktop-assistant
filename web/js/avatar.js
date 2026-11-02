// Renders a VRM model with three.js and drives its face: blinking, idle motion,
// a mood expression that eases in and out, and a mouth value the speech player
// sets every frame.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

const MOODS = ["happy", "angry", "sad", "relaxed", "surprised"];

export class Avatar {
  constructor(canvas) {
    // alpha: true now so the later transparent desktop window needs no changes here.
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.1, 20);
    this.camera.position.set(0, 1.35, 1.6);

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(0.6, 1.8, 2.0);
    this.scene.add(key, new THREE.AmbientLight(0xffffff, 0.9));

    this.timer = new THREE.Timer();
    this.vrm = null;
    this.mouth = 0; // 0..1, set by the speech player
    this._nextBlink = 2;
    this._blinkT = -1;
    // Mood expressions: each preset's weight eases toward its target every frame.
    this._exprTarget = {};   // weight while quiet
    this._exprTalking = {};  // weight while speaking (usually lower)
    this.talking = false;    // set by the page while speech plays
    this._exprNow = {};
    this._warned = new Set();

    new ResizeObserver(() => this._resize()).observe(canvas);
    this._resize();
    this.renderer.setAnimationLoop(() => this._tick());
  }

  async load(url) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const gltf = await loader.loadAsync(url);
    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error(`${url} is not a VRM file`);

    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm); // VRM 0.x models face the other way
    vrm.scene.traverse((o) => { o.frustumCulled = false; });

    // Many models' smiles open the mouth. Lip sync opening it again on top looks
    // wrong, so a mood expression scales the lip-sync mouth down by its own
    // weight (VRM's "blend" mouth override) instead of stacking with it.
    for (const preset of MOODS) {
      const ex = vrm.expressionManager?.getExpression(preset);
      if (ex && ex.overrideMouth === "none") ex.overrideMouth = "blend";
    }

    this.scene.add(vrm.scene);
    this.vrm = vrm;
    this._restPose();
    this._frame();
  }

  // Show one mood preset (happy, sad, relaxed, surprised, angry) at `weight`,
  // or at `talkWeight` while she's speaking, easing out whatever was showing
  // before. "neutral" or weight 0 relaxes the face.
  setExpression(name, weight = 0.6, talkWeight = weight / 2) {
    for (const preset of MOODS) { this._exprTarget[preset] = 0; this._exprTalking[preset] = 0; }
    if (name === "neutral" || !weight) return;
    if (this.vrm && !this.vrm.expressionManager?.getExpression(name)) {
      // e.g. VRM 0.x models have no "surprised"; map that mood to another
      // preset in the persona file's expressions: section.
      if (!this._warned.has(name)) console.warn(`This avatar has no "${name}" expression.`);
      this._warned.add(name);
      return;
    }
    const clamp = (w) => Math.max(0, Math.min(1, w));
    this._exprTarget[name] = clamp(weight);
    this._exprTalking[name] = clamp(talkWeight);
  }

  // Which expressions this model has, for picking presets in the persona file.
  expressionNames() {
    return Object.keys(this.vrm?.expressionManager?.expressionMap ?? {});
  }

  // Lower the arms out of the T-pose. Normalized bones share one convention
  // across VRM 0.x and 1.0 models, so these angles work for any model.
  _restPose() {
    const bone = (n) => this.vrm.humanoid.getNormalizedBoneNode(n);
    bone("leftUpperArm")?.rotation.set(0, 0, -1.25);
    bone("rightUpperArm")?.rotation.set(0, 0, 1.25);
    bone("leftLowerArm")?.rotation.set(0, 0, -0.15);
    bone("rightLowerArm")?.rotation.set(0, 0, 0.15);
  }

  // Aim the camera at the head so any model height frames the same way.
  _frame() {
    this.vrm.update(0);
    const head = this.vrm.humanoid.getNormalizedBoneNode("head");
    const p = new THREE.Vector3();
    head.getWorldPosition(p);
    // Head and shoulders, with the eyes about a third of the way down the frame.
    this.camera.position.set(0, p.y - 0.05, p.z + 1.7);
    this.camera.lookAt(0, p.y - 0.12, p.z);
  }

  _resize() {
    const c = this.renderer.domElement;
    const w = c.clientWidth || 1, h = c.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _tick() {
    this.timer.update();
    const dt = Math.min(this.timer.getDelta(), 0.1);
    const t = this.timer.getElapsed();
    if (this.vrm) {
      const em = this.vrm.expressionManager;
      em?.setValue("aa", this.mouth);
      em?.setValue("blink", this._blink(dt));
      this._easeExpressions(em, dt);

      // Idle motion: slow breathing and a little head drift.
      const h = this.vrm.humanoid;
      h.getNormalizedBoneNode("chest")?.rotation.set(Math.sin(t * 1.6) * 0.015, 0, 0);
      h.getNormalizedBoneNode("neck")?.rotation.set(
        Math.sin(t * 0.7) * 0.03, Math.sin(t * 0.43) * 0.06, Math.sin(t * 0.31) * 0.02);
      this.vrm.update(dt);
    }
    this.renderer.render(this.scene, this.camera);
  }

  _easeExpressions(em, dt) {
    if (!em) return;
    for (const preset of MOODS) {
      const now = this._exprNow[preset] ?? 0;
      const target = (this.talking ? this._exprTalking : this._exprTarget)[preset] ?? 0;
      if (now === target) continue;
      // Ease in over about 0.3 s and out over about 0.6 s: faces light up
      // faster than they settle.
      const rate = target > now ? 10 : 5;
      let next = now + (target - now) * Math.min(1, rate * dt);
      if (Math.abs(target - next) < 0.005) next = target;
      this._exprNow[preset] = next;
      em.setValue(preset, next);
    }
  }

  _blink(dt) {
    this._nextBlink -= dt;
    if (this._blinkT < 0 && this._nextBlink <= 0) {
      this._blinkT = 0;
      this._nextBlink = 2 + Math.random() * 4;
    }
    if (this._blinkT < 0) return 0;
    this._blinkT += dt;
    const d = 0.15; // seconds for a full blink
    if (this._blinkT > d) { this._blinkT = -1; return 0; }
    return Math.sin((this._blinkT / d) * Math.PI);
  }
}
