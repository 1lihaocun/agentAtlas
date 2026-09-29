import { useRef, useState } from 'react';
import { Dropdown, Input } from 'antd';
import { Check, ChevronDown } from 'lucide-react';
import './FilterDropdown.css';

interface FilterOption { value: string; label: string; description?: string }
interface FilterDropdownProps {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
  searchable?: boolean;
}

export function FilterDropdown({ label, value, options, onChange, searchable = false }: FilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = options.find(option => option.value === value);
  const selectedLabel = selected?.label ?? value;
  const matching = options.filter(option => !option.value || `${option.label} ${option.description || ''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  function changeOpen(next: boolean) {
    setOpen(next);
    if (!next) setSearch('');
  }
  function close() { changeOpen(false); trigger.current?.focus(); }

  return <div className="filter-field"><span>{label}</span>
    <Dropdown trigger={['click']} open={open} onOpenChange={changeOpen} autoFocus={!searchable}
      align={{ overflow: { adjustX: true, adjustY: true, shiftX: true, shiftY: true } }}
      destroyOnHidden placement="bottomLeft" classNames={{ root: 'filter-dropdown' }}
      menu={{
        selectable: true, selectedKeys: [value], 'aria-label': `${label}选项`,
        style: { flex: '1 1 auto', minHeight: 0, overflowY: 'scroll', scrollbarGutter: 'stable', boxShadow: 'none' },
        items: matching.length ? matching.map(option => ({
          key: option.value,
          label: <span className="filter-option" title={option.description ? `${option.label}\n${option.description}` : option.label}>
            <span className="filter-option-text">
              <span className={option.description ? 'filter-option-name' : undefined}>{option.label}</span>
              {option.description && <span className="filter-option-path">{option.description}</span>}
            </span>
            <Check size={14} aria-hidden="true" style={{ visibility: option.value === value ? 'visible' : 'hidden' }} />
          </span>,
        })) : [{ key: '__empty', label: '没有匹配的选项', disabled: true }],
        onClick: ({ key }) => { onChange(key); close(); },
      }}
      popupRender={menu => <div className="filter-popup" onKeyDown={event => {
        if (event.key === 'Escape') { event.stopPropagation(); close(); }
      }}>
        {searchable && <div className="filter-search"><Input autoFocus allowClear aria-label={`搜索${label}`}
          placeholder={`搜索${label}`} value={search} onChange={event => setSearch(event.target.value)} /></div>}
        {menu}
      </div>}>
      <button ref={trigger} type="button" className="filter-trigger" aria-label={`${label}：${selectedLabel}`}
        aria-haspopup="menu" aria-expanded={open} title={selectedLabel}
        onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); changeOpen(true); } }}>
        <span>{selectedLabel}</span><ChevronDown size={14} aria-hidden="true" />
      </button>
    </Dropdown>
  </div>;
}
