import React, { useEffect, useRef } from 'react';
import { 
  Crosshair, 
  TrendingUp, 
  Pencil, 
  Type, 
  Ruler, 
  Trash2,
  Undo2
} from 'lucide-react';
import gsap from 'gsap';

export default function LeftToolbar() {
  const toolbarRef = useRef(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(toolbarRef.current, {
        x: -50,
        opacity: 0,
        duration: 0.6,
        ease: "power3.out"
      });
      
      gsap.from(".toolbar-btn", {
        scale: 0.8,
        opacity: 0,
        duration: 0.3,
        stagger: 0.05,
        ease: "back.out(1.5)",
        delay: 0.2
      });
    }, toolbarRef);
    return () => ctx.revert();
  }, []);

  return (
    <div className="left-toolbar" ref={toolbarRef}>
      <button className="toolbar-btn active" title="Crosshair">
        <Crosshair size={20} />
      </button>
      <button className="toolbar-btn" title="Trend Line">
        <TrendingUp size={20} />
      </button>
      <button className="toolbar-btn" title="Brush">
        <Pencil size={20} />
      </button>
      <button className="toolbar-btn" title="Text">
        <Type size={20} />
      </button>
      <button className="toolbar-btn" title="Measure">
        <Ruler size={20} />
      </button>
      
      <div style={{ flex: 1 }} /> {/* Spacer */}
      
      <button className="toolbar-btn" title="Undo">
        <Undo2 size={20} />
      </button>
      <button className="toolbar-btn" title="Remove Drawings">
        <Trash2 size={20} />
      </button>
    </div>
  );
}
