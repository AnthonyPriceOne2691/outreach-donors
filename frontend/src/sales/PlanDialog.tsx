/**
 * Окно кнопки гипотезы плана — каркас один у «Собрать ещё», «Качество сайтов» и «Добавить ключи»:
 * заголовок, отказ сервера плашкой над полями, поля и «Отмена» с главной кнопкой.
 *
 * Пока сервер отвечает, окно не закрывается: его отказ иначе не увидели бы нигде (так же у окна
 * гипотезы). Отказ — словами сервера; набранное остаётся на месте.
 */

import { Alert, Button, Group, Modal, Stack } from '@mantine/core';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';

interface Props {
  title: string;
  /** Заголовок плашки отказа: «Цель не поправлена». */
  refused: string;
  error: Error | null;
  busy: boolean;
  /** Главная кнопка открыта: вписано то, что нужно. */
  ready: boolean;
  /** Слово главной кнопки. */
  action: string;
  onSubmit: () => void;
  onClose: () => void;
  children: ReactNode;
}

export function PlanDialog(props: Props) {
  const { title, refused, error, busy, ready, action, onSubmit, onClose, children } = props;
  const close = () => {
    if (!busy) onClose();
  };
  return (
    <Modal opened onClose={close} title={title}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) onSubmit();
        }}
      >
        <Stack gap={FIELD_GAP}>
          {error !== null && (
            <Alert color="red" title={refused}>
              {refusalOf(error)}
            </Alert>
          )}
          {children}
          <Group justify="flex-end" mt={ACTIONS_GAP}>
            <Button variant="subtle" className="press" disabled={busy} onClick={close}>
              Отмена
            </Button>
            <Button type="submit" className="press" disabled={!ready} loading={busy}>
              {action}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
