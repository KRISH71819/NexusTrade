import { useEffect, useRef, useState } from "react";
import { gsap } from "gsap";

/**
 * Animated count-up from 0 to target value.
 * Returns [ref, displayValue].
 */
export function useCountUp(target, { duration = 1.5, decimals = 0, prefix = "", suffix = "" } = {}) {
  const [display, setDisplay] = useState(`${prefix}0${suffix}`);
  const objRef = useRef({ val: 0 });

  useEffect(() => {
    const num = Number(target) || 0;
    gsap.to(objRef.current, {
      val: num,
      duration,
      ease: "power2.out",
      onUpdate() {
        const v = objRef.current.val;
        const formatted = Math.abs(v) >= 1000
          ? v.toLocaleString("en-IN", { maximumFractionDigits: decimals })
          : v.toFixed(decimals);
        setDisplay(`${prefix}${formatted}${suffix}`);
      },
    });
  }, [target, duration, decimals, prefix, suffix]);

  return display;
}
