import { useEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/**
 * Reveal children with a staggered fade-up animation on scroll.
 * Usage: const ref = useGsapReveal(); <div ref={ref}>...</div>
 */
export function useGsapReveal({ stagger = 0.1, y = 40, duration = 0.8, delay = 0 } = {}) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const children = el.children.length > 0 ? el.children : [el];

    gsap.set(children, { opacity: 0, y });

    const tween = gsap.to(children, {
      opacity: 1,
      y: 0,
      duration,
      stagger,
      delay,
      ease: "power3.out",
      scrollTrigger: {
        trigger: el,
        start: "top 85%",
        toggleActions: "play none none none",
      },
    });

    return () => {
      tween.kill();
      ScrollTrigger.getAll().forEach((t) => {
        if (t.trigger === el) t.kill();
      });
    };
  }, [stagger, y, duration, delay]);

  return ref;
}

/**
 * Fade-in a single element (no scroll trigger, just on mount).
 */
export function useGsapFadeIn({ duration = 0.6, delay = 0, y = 20 } = {}) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    gsap.fromTo(
      el,
      { opacity: 0, y },
      { opacity: 1, y: 0, duration, delay, ease: "power2.out" }
    );
  }, [duration, delay, y]);

  return ref;
}
