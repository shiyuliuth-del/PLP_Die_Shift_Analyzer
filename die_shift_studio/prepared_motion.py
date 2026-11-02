"""Validated geometry keyframes; solver time never consumes playback time."""
from contextlib import nullcontext
import numpy as np
from .animation import motion_timing


def motion_packet(engine,start,end,settings,view_span,cache):
    start=np.array(start,float,copy=True);end=np.array(end,float,copy=True)
    start[start==0]=0.;end[end==0]=0.
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
    a,b=sorted((start.tobytes(),end.tobytes()))
    key=('validated-motion-v25',engine.signature,engine.cache_key(),a,b,steps)
    packet=cache.get(key)
    if packet is not None and start.tobytes()!=a:
        packet=dict(indices=[steps-i for i in reversed(packet['indices'])],
                    frames=[(1-f,frame) for f,frame in reversed(packet['frames'])],complete=packet['complete'])
    return start,end,duration,steps,requested_steps,key,packet


def cached_motion(engine,start,end,settings,view_span,cache):
    packet=motion_packet(engine,start,end,settings,view_span,cache)[-1]
    return packet if packet and packet['complete'] else None


def prepare_motion(engine,start,end,settings,view_span,cache,progress,publish=None,cancelled=None):
    start,end,duration,steps,requested_steps,key,packet=motion_packet(engine,start,end,settings,view_span,cache)
    by_index={} if packet is None else dict(zip(packet['indices'],packet['frames']))
    cached=bool(packet and packet['complete']);saved=[];added={}
    fractions=np.arange(1,steps,dtype=float)/steps
    fractions=fractions*fractions*(3-2*fractions)
    if not cached:
        try:
            for i,fraction in enumerate(fractions,1):
                progress(round(95*(i-1)/len(fractions)),f'准备动画 {i}/{len(fractions)}')
                if i in by_index:frame=by_index[i][1]
                else:
                    frame=engine.frame(start*(1-fraction)+end*fraction,lambda n,t:progress(round(95*(i-1+n/100)/len(fractions)),f'动画帧 {i}/{len(fractions)} · {t}'),quick=True)
                    slot=i if start.tobytes()==key[3] else steps-i
                    added[slot]=(float(fractions[slot-1]),frame)
                saved.append((float(fraction),frame))
                if publish is not None:
                    publish(frame)
                    if cancelled is not None:cancelled.wait(duration/steps/1000)
        finally:
            # Both directions share one file. Merge interrupted/concurrent work
            # without replacing a complete packet or registering a read as new.
            if added:
                with getattr(cache,'lock',nullcontext()):
                    previous=cache.get(key)
                    slots={} if previous is None else dict(zip(previous['indices'],previous['frames']))
                    if added.keys()-slots.keys():
                        slots.update(added);indices=sorted(slots)
                        cache[key]=dict(indices=indices,frames=[slots[i] for i in indices],complete=len(slots)==len(fractions))
    else:
        saved=packet['frames']
    if cached and publish is not None:
        for i,(_,frame) in enumerate(saved):
            progress(round(95*i/max(1,len(saved))),'重用已校验动画缓存')
            publish(frame)
            if cancelled is not None:cancelled.wait(duration/steps/1000)
    progress(100,'型变动画帧准备完成')
    return dict(prepared=True,streamed=publish is not None,count=len(saved),frames=saved,duration=duration,steps=steps,requested_steps=requested_steps,cached=cached)
