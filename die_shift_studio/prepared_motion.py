"""Validated geometry keyframes; solver time never consumes playback time."""
import numpy as np
from .animation import motion_timing


def prepare_motion(engine,start,end,settings,view_span,cache,progress,publish=None,cancelled=None):
    start=np.asarray(start,float);end=np.asarray(end,float)
    travel=float(np.max(np.linalg.norm(end[:,:2]-start[:,:2],axis=1)+engine.bound.owner_radii*np.abs(end[:,2]-start[:,2])))
    duration,steps=motion_timing(settings,travel,view_span)
    requested_steps=max(2,steps)
    geometry=getattr(engine.bound,'cache',None)
    if geometry is not None:
        # Geometry size, not just FPS, controls the temporary prepared bundle.
        # The profile is fixed for this geometry session so replay keys stay
        # stable when unrelated applications change the currently free RAM.
        budget=min(256*1024**2,max(16*1024**2,geometry.profile['available_memory_mb']*1024**2*.08))
        estimate=max(1024**2,geometry.bytes*8)
        steps=max(2,min(requested_steps,int(budget/estimate)))
    else:steps=requested_steps
    key=('validated-motion-v19',engine.signature,engine.cache_key(),start.tobytes(),end.tobytes(),steps)
    packet=cache.get(key);saved=[] if packet is None else list(packet['frames']);cached=bool(packet and packet['complete'])
    fractions=np.arange(1,steps,dtype=float)/steps
    fractions=fractions*fractions*(3-2*fractions)
    if not cached:
        try:
            for i,fraction in enumerate(fractions):
                progress(round(95*i/max(1,len(fractions))),f'准备型变动画 {i+1}/{len(fractions)}')
                if i<len(saved):frame=saved[i][1]
                else:
                    frame=engine.frame(start*(1-fraction)+end*fraction,lambda n,t:progress(round(95*(i+n/100)/max(1,len(fractions))),f'动画帧 {i+1}/{len(fractions)} · '+t),quick=True)
                    saved.append((float(fraction),frame))
                if publish is not None:
                    publish(frame)
                    if cancelled is not None:cancelled.wait(duration/steps/1000)
        finally:
            # Write one bundle, including on cancellation; do not serialize
            # the growing animation quadratically once per generated frame.
            if saved:cache[key]=dict(frames=saved,complete=len(saved)==len(fractions))
    elif publish is not None:
        for i,(_,frame) in enumerate(saved):
            progress(round(95*i/max(1,len(saved))),'重用已校验动画缓存')
            publish(frame)
            if cancelled is not None:cancelled.wait(duration/steps/1000)
    progress(100,'型变动画帧准备完成')
    return dict(prepared=True,streamed=publish is not None,count=len(saved),frames=saved,duration=duration,steps=steps,requested_steps=requested_steps,cached=cached)
