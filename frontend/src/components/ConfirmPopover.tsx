/**
 * Подтверждение действия с последствиями — маленьким окном у самой кнопки.
 *
 * Аудит экранов 09.10.2026: «Сбросить пароль» и выключатель учётки срабатывали с
 * первого нажатия. Окно на весь экран для такого — лишнее, а без подтверждения
 * промах стоит человеку входа. Правила (NN/g, GOV.UK): текст называет последствие,
 * на кнопке — глагол, а не «ОК»; кнопки «по умолчанию» нет — Enter ничего не
 * подтверждает сам, фокус встаёт на «Отмену».
 */

import { Button, Group, Popover, Stack, Text } from '@mantine/core';
import { useId, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';

interface FrameProps {
  opened: boolean;
  onChange: (opened: boolean) => void;
  /** Что случится — словами последствия. */
  message: ReactNode;
  /** Мелким под вопросом — из чего он сложился; без него строки нет. */
  details?: ReactNode;
  /** Глагол на кнопке подтверждения: «Сбросить», «Отключить»; `null` — подтверждать нечего:
   *  окно только сообщает, и кнопка одна — «Понятно». */
  confirm: string | null;
  /** Последствие не отменить одним нажатием — кнопка красная. */
  danger?: boolean;
  onConfirm: () => void;
  /** Кнопка или переключатель, у которого окно. */
  target: ReactElement;
}

/**
 * Окно у кнопки, открытое снаружи: его держит `ConfirmPopover`, а смета «найти»
 * (`donors/SearchBudget.tsx`) открывает его, когда сервер ответил. Одна рамка на оба —
 * две копии разъехались бы на первой правке (25.11.2026).
 */
export function ConfirmFrame({
  opened,
  onChange,
  message,
  details,
  confirm,
  danger = false,
  onConfirm,
  target,
}: FrameProps) {
  // Окно называется своим вопросом, а не кнопкой, которая его открыла: так его и читает
  // программа чтения с экрана — «Отключить …? Учётка перестанет пускать…».
  const question = useId();
  return (
    <Popover
      opened={opened}
      onChange={onChange}
      position="bottom"
      withArrow
      trapFocus
      returnFocus
      radius="lg"
      shadow="md"
      transitionProps={{ transition: 'pop', duration: 160 }}
    >
      <Popover.Target>{target}</Popover.Target>
      <Popover.Dropdown className="glassSolid" aria-labelledby={question}>
        <Stack gap="sm" maw={300}>
          <Text size="sm" id={question}>
            {message}
          </Text>
          {details ? (
            <Text size="xs" c="dimmed">
              {details}
            </Text>
          ) : null}
          <Group gap="xs" justify="flex-end" wrap="nowrap">
            <Button
              size="compact-sm"
              variant="default"
              data-autofocus
              onClick={() => onChange(false)}
            >
              {confirm === null ? 'Понятно' : 'Отмена'}
            </Button>
            {confirm === null ? null : (
              <Button
                size="compact-sm"
                color={danger ? 'red' : 'lagoon'}
                className="press"
                onClick={() => {
                  onChange(false);
                  onConfirm();
                }}
              >
                {confirm}
              </Button>
            )}
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

interface Props {
  /** Что случится — словами последствия. */
  message: ReactNode;
  /** Глагол на кнопке подтверждения: «Сбросить», «Отключить». */
  confirm: string;
  /** Последствие не отменить одним нажатием — кнопка красная. */
  danger?: boolean;
  onConfirm: () => void;
  /** Кнопка или переключатель, который просит подтверждения: получает `ask`. */
  children: (ask: () => void) => ReactElement;
}

export function ConfirmPopover({ message, confirm, danger = false, onConfirm, children }: Props) {
  const [opened, setOpened] = useState(false);
  return (
    <ConfirmFrame
      opened={opened}
      onChange={setOpened}
      message={message}
      confirm={confirm}
      danger={danger}
      onConfirm={onConfirm}
      target={children(() => setOpened(true))}
    />
  );
}
