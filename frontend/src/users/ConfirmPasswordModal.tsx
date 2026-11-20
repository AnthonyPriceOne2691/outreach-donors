/**
 * Подтверждение действия админа своим паролем (аудит 20.11.2026, №10).
 *
 * Роль, права, включение и отключение учётки, сброс чужого пароля — то, ради чего крадут
 * пропуск админа: с ним вор выдаёт себе права, отбирает их у хозяина или сбрасывает чужой
 * пароль и входит по разовому. Пароля в пропуске нет, и без него сервер этих действий не делает.
 * Окно спрашивает пароль перед каждым таким действием и само же его подтверждает: текст называет
 * последствие, на кнопке — глагол, а не «ОК» (правила `components/ConfirmPopover`).
 *
 * Отказ сервера — здесь же, и окно остаётся открытым: неверный пароль набирают заново, а не
 * открывают окно сначала. Набранный пароль после отказа стирается.
 */

import { Alert, Modal, PasswordInput, Stack, Text } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { FIELD_GAP } from '../components/formRhythm';
import { SaveActions } from '../components/SaveActions';

export interface Confirming {
  /** Вопрос — он же имя окна: «Отключить ivan@site.com?». */
  question: string;
  /** Что случится — словами последствия. */
  consequence: string;
  /** Глагол на кнопке: «Отключить», «Сбросить», «Сменить роль». */
  confirm: string;
  /** Последствие не отменить одним нажатием — кнопка красная. */
  danger?: boolean;
  /** Само действие с паролем админа; отказ — исключением, окно покажет его текст. */
  run: (password: string) => Promise<unknown>;
}

interface Props {
  confirming: Confirming | null;
  onClose: () => void;
}

export function ConfirmPasswordModal({ confirming, onClose }: Props) {
  return (
    <Modal opened={confirming !== null} onClose={onClose} title={confirming?.question}>
      {/* Форма живёт, пока открыто окно: следующее действие начинает с пустого пароля. */}
      {confirming !== null && <ConfirmForm confirming={confirming} onClose={onClose} />}
    </Modal>
  );
}

function ConfirmForm({ confirming, onClose }: { confirming: Confirming; onClose: () => void }) {
  const [refusal, setRefusal] = useState<string | null>(null);
  const form = useForm({
    initialValues: { password: '' },
    validate: {
      password: (value) => (value === '' ? 'Впишите свой пароль — тот, которым входите' : null),
    },
  });

  const submit = form.onSubmit(async ({ password }) => {
    setRefusal(null);
    try {
      await confirming.run(password);
      onClose();
    } catch (error) {
      setRefusal(refusalOf(error));
      form.setFieldValue('password', '');
    }
  });

  return (
    <form onSubmit={submit}>
      <Stack gap={FIELD_GAP}>
        <Text size="sm">{confirming.consequence}</Text>
        {refusal !== null && (
          <Alert color="red" title="Не сделано">
            {refusal}
          </Alert>
        )}
        <PasswordInput
          label="Ваш пароль"
          description="Действие подтверждается паролем, которым вы входите"
          autoComplete="current-password"
          data-autofocus
          {...form.getInputProps('password')}
        />
        <SaveActions
          onCancel={onClose}
          saving={form.submitting}
          locked={null}
          saveLabel={confirming.confirm}
          danger={confirming.danger === true}
        />
      </Stack>
    </form>
  );
}
