/**
 * «Отмена» и «Сохранить» внизу окна правки раздела — одно место на окна: запись базы знаний,
 * лид. Кнопка записи — `submit` формы окна: Enter в поле сохраняет, как нажатие.
 */

import { Button, Group } from '@mantine/core';

interface Props {
  /** Есть что сохранить и есть право — кнопка живая. */
  ready: boolean;
  /** Сохранение ждёт сервера. */
  busy: boolean;
  onCancel: () => void;
  /** Подпись кнопки записи: «Сохранить», у новой записи — «Завести». */
  label?: string;
}

export function SaveActions({ ready, busy, onCancel, label = 'Сохранить' }: Props) {
  return (
    <Group justify="flex-end" mt="sm">
      <Button variant="subtle" className="press" onClick={onCancel}>
        Отмена
      </Button>
      <Button type="submit" className="press" disabled={!ready} loading={busy}>
        {label}
      </Button>
    </Group>
  );
}
