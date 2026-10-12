/* Safe semantic text rendering; every string remains textContent. */
export function fillStructuredText(container,text,format,{fixedFrame=false}={}){
  container.replaceChildren();
  const parts=format?.parts?.length?format.parts:[{kind:'paragraph',text}];
  container.classList.add('structured-text');
  container.dataset.structurePreserved=String(format?.preserved!==false);
  for(const part of parts){
    const row=document.createElement('div');row.className='structured-part';
    if(part.kind==='list_item'){
      row.classList.add('structured-list-item');row.style.marginLeft=`${Math.min(8,Math.max(0,(part.indent_em || 0)+(fixedFrame?0:format?.card_indent_em || 0)))}em`;
      row.style.gridTemplateColumns=`${Math.max(1,part.hanging_em || 1)}em minmax(0,1fr)`;
      const marker=document.createElement('span');marker.className='structured-marker';marker.textContent=part.marker || '';row.appendChild(marker);
      const content=document.createElement('span');content.className='structured-item-text';content.textContent=part.text || '';row.appendChild(content);
    }else{
      row.style.textIndent=`${Math.min(4,Math.max(0,part.first_indent_em || 0))}em`;row.textContent=part.text || '';
    }
    container.appendChild(row);
  }
  return container;
}
