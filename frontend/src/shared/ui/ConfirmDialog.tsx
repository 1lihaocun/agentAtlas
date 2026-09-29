import * as Dialog from '@radix-ui/react-dialog';
import type { ReactNode } from 'react';

export function ConfirmDialog({ open, onOpenChange, title, children, onConfirm, pending = false, confirmDisabled = false }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; children: ReactNode; onConfirm: () => void; pending?: boolean; confirmDisabled?: boolean;
}) {
  return <Dialog.Root open={open} onOpenChange={next => { if (!pending) onOpenChange(next); }}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content">
      <Dialog.Title>{title}</Dialog.Title><Dialog.Description asChild><div className="dialog-description">{children}</div></Dialog.Description>
      <div className="dialog-actions"><Dialog.Close asChild><button type="button" disabled={pending}>取消</button></Dialog.Close>
        <button type="button" className="primary" onClick={onConfirm} disabled={pending || confirmDisabled}>{pending ? '正在执行…' : '确认执行'}</button></div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
