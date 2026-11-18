/**
 * Окно кнопки гипотезы плана — каркас один у «Собрать ещё», «Качество сайтов» и «Добавить ключи»:
 * заголовок с именем гипотезы, отказ сервера плашкой над полями, поля и «Отмена» с главной кнопкой.
 *
 * Пока сервер отвечает, окно не закрывается: его отказ иначе не увидели бы нигде (так же у окна
 * гипотезы). Отказ — словами сервера; набранное остаётся на месте, плашка — до следующего нажатия.
 *
 * **Заголовок — короткий, имя гипотезы — строкой под ним, приглушённо** (стенд 17.11): «Собрать ещё:
 * «…»» с длинным именем вставал в четыре строки с межстрочным 1 и слипался. Строка имени — в
 * заголовке окна: окно и для читалки называется «Собрать ещё «…»».
 *
 * **В окне одна прокрутка** (`OneScrollForm`): заголовок, отказ и кнопки стоят, прокручиваются поля.
 * Плашка отказа — без заголовка, одним абзацем: «Качество не сохранено: слова сервера». Стенд 17.11:
 * «Качество сайтов» на 1366 × 768 после отказа — 691 px с прокруткой окна, и «Сохранить» уходил
 * под край.
 */

import { Alert, Button, Group, Modal, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';
import { OneScrollForm } from './OneScrollForm';

interface Props {
  /** Что делает окно: «Качество сайтов». */
  title: string;
  /** Имя гипотезы — строкой под заголовком. */
  name: string;
  /** Начало отказа: «Цель не поправлена». */
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

/** Заголовок окна и имя гипотезы под ним: длинное имя рвётся где угодно, межстрочный — текста. */
function Title({ title, name }: { title: string; name: string }) {
  return (
    <>
      {title}
      <Text
        component="span"
        display="block"
        size="sm"
        fw={400}
        c="dimmed"
        mt={4}
        style={{ overflowWrap: 'anywhere' }}
      >
        «{name}»
      </Text>
    </>
  );
}

export function PlanDialog(props: Props) {
  const { title, name, refused, error, busy, ready, action, onSubmit, onClose, children } = props;
  const close = () => {
    if (!busy) onClose();
  };
  return (
    <Modal
      opened
      onClose={close}
      title={<Title title={title} name={name} />}
      classNames={{ content: 'oneScroll' }}
    >
      <OneScrollForm
        gap={FIELD_GAP}
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) onSubmit();
        }}
        top={
          error !== null && (
            <Alert color="red" py="xs">
              <strong>{refused}</strong>: {refusalOf(error)}
            </Alert>
          )
        }
        footer={
          <Group justify="flex-end" mt={ACTIONS_GAP}>
            <Button variant="subtle" className="press" disabled={busy} onClick={close}>
              Отмена
            </Button>
            <Button type="submit" className="press" disabled={!ready} loading={busy}>
              {action}
            </Button>
          </Group>
        }
      >
        {children}
      </OneScrollForm>
    </Modal>
  );
}
