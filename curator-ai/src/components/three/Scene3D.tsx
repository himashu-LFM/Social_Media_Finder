"use client";

/**
 * Scene3D — the holographic-globe + particle field rendered with React Three
 * Fiber (Three.js). Loaded lazily and client-only via BackgroundScene.
 *
 * Design: a low-poly wireframe "globe" in the brand yellow with a soft additive
 * halo (faked bloom — no post-processing dependency), an ambient star/particle
 * field, three lights (ambient + point + directional), a slow auto-rotation and
 * a subtle mouse-parallax camera rig. All motion is gated by the `animate` prop
 * (reduced-motion) and the Canvas `frameloop` (paused when the tab is hidden).
 */

import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Float, PointMaterial, Points } from "@react-three/drei";
import * as THREE from "three";

const BRAND = "#f2d100"; // ListenFirst yellow
const ACCENT = "#7dd3fc"; // subtle sky accent (already used across the app)

/** Deterministic PRNG (mulberry32) — pure, so particle positions are stable
 *  across renders and don't trip React's no-impure-calls-in-render rule. */
function makeRng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type SceneProps = {
  animate?: boolean;
  frameloop?: "always" | "never" | "demand";
};

/** Low-poly wireframe globe with a soft additive glow halo. */
function Globe({ animate }: { animate: boolean }) {
  const group = useRef<THREE.Group>(null);

  useFrame((_, delta) => {
    if (group.current && animate) {
      group.current.rotation.y += delta * 0.15; // slow rotation
    }
  });

  return (
    <Float speed={animate ? 1.1 : 0} rotationIntensity={0.35} floatIntensity={0.6}>
      <group ref={group}>
        {/* Low-poly wireframe shell (emissive so it reads without strong lighting). */}
        <mesh>
          <icosahedronGeometry args={[1.6, 1]} />
          <meshStandardMaterial
            color={BRAND}
            emissive={BRAND}
            emissiveIntensity={0.55}
            wireframe
            transparent
            opacity={0.9}
          />
        </mesh>
        {/* Faint solid core. */}
        <mesh scale={0.98}>
          <icosahedronGeometry args={[1.6, 2]} />
          <meshBasicMaterial color={BRAND} transparent opacity={0.05} />
        </mesh>
        {/* Additive halo — the "bloom/glow". */}
        <mesh scale={1.35}>
          <sphereGeometry args={[1.6, 32, 32]} />
          <meshBasicMaterial
            color={BRAND}
            transparent
            opacity={0.04}
            blending={THREE.AdditiveBlending}
            depthWrite={false}
          />
        </mesh>
      </group>
    </Float>
  );
}

/** Ambient floating particle field. */
function ParticleField({ animate, count = 1400 }: { animate: boolean; count?: number }) {
  const ref = useRef<THREE.Points>(null);

  const positions = useMemo(() => {
    const rand = makeRng(0x9e3779b9);
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const radius = 4 + rand() * 6;
      const theta = rand() * Math.PI * 2;
      const phi = Math.acos(2 * rand() - 1);
      arr[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = radius * Math.cos(phi);
    }
    return arr;
  }, [count]);

  useFrame((_, delta) => {
    if (ref.current && animate) {
      ref.current.rotation.y += delta * 0.02;
      ref.current.rotation.x += delta * 0.005;
    }
  });

  return (
    <Points ref={ref} positions={positions} stride={3} frustumCulled={false}>
      <PointMaterial
        transparent
        color={ACCENT}
        size={0.03}
        sizeAttenuation
        depthWrite={false}
        opacity={0.8}
        blending={THREE.AdditiveBlending}
      />
    </Points>
  );
}

/** Mouse-parallax camera rig — eases the camera toward the pointer. */
function ParallaxRig({ animate }: { animate: boolean }) {
  useFrame((state) => {
    if (!animate) return;
    const targetX = state.pointer.x * 0.7;
    const targetY = state.pointer.y * 0.45;
    state.camera.position.x += (targetX - state.camera.position.x) * 0.04;
    state.camera.position.y += (targetY - state.camera.position.y) * 0.04;
    state.camera.lookAt(0, 0, 0);
  });
  return null;
}

export default function Scene3D({ animate = true, frameloop = "always" }: SceneProps) {
  return (
    <Canvas
      dpr={[1, 1.5]} // clamp device-pixel-ratio for performance
      camera={{ position: [0, 0, 6], fov: 45 }}
      gl={{ alpha: true, antialias: true, powerPreference: "high-performance" }}
      frameloop={frameloop}
      style={{ width: "100%", height: "100%" }}
    >
      <ambientLight intensity={0.6} />
      <pointLight position={[6, 6, 6]} intensity={60} decay={0} color={BRAND} />
      <pointLight position={[-6, -4, 2]} intensity={25} decay={0} color={ACCENT} />
      <directionalLight position={[0, 4, 5]} intensity={1.4} />
      <Globe animate={animate} />
      <ParticleField animate={animate} />
      <ParallaxRig animate={animate} />
    </Canvas>
  );
}
