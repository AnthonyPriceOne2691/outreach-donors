/**
 * Включение входа с кодом: ключ в приложение, первый код, коды восстановления (23.11.2026).
 *
 * Один экран на два пути: обязательный (роль требует код — сразу после пароля, по вызову входа)
 * и по своей воле (меню у почты → «Вход с кодом»). Кто начал включение и чем подтверждать
 * первый код, решает тот, кто экран открыл (`confirm`).
 *
 * Ключ показан группами по четыре — его переписывают руками; ссылка `otpauth://` на телефоне
 * сразу открывает приложение. QR-кода нет: это новая зависимость, и он встанет отдельной правкой
 * на место, отмеченное ниже, — той же ссылкой картинкой.
 *
 * Код включён — пропуск из ответа берётся сразу (`onEnabled`): прежние пропуска учётки сервер
 * погасил, и фоновый запрос со старым выбросил бы на вход раньше, чем человек сохранит коды.
 * Коды показываются один раз; дальше — `onDone`.
 */

import { Button, Code, CopyButton, Group, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';

import type { TotpEnabled, TotpSetup } from '../api/types';
import { FIELD_GAP } from '../components/formRhythm';
import { SIX_DIGITS, digitsOf } from './CodeStep';
import { CodeRefusalAlert, useCodeRefusal } from './codeRefusal';
import { RecoveryCodes } from './RecoveryCodes';

/** Ключ группами по четыре — так его легче переписать в приложение. */
export function keyGroups(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, '$1 ');
}

interface Props {
  setup: TotpSetup;
  confirm: (code: string) => Promise<TotpEnabled>;
  onEnabled: (enabled: TotpEnabled) => void;
  onDone: (enabled: TotpEnabled) => void;
}

export function TotpEnrollment({ setup, confirm, onEnabled, onDone }: Props) {
  const [enabled, setEnabled] = useState<TotpEnabled | null>(null);
  const refusal = useCodeRefusal();
  const form = useForm({
    initialValues: { code: '' },
    validate: {
      code: (value) => (SIX_DIGITS.test(digitsOf(value)) ? null : 'Шесть цифр из приложения'),
    },
  });

  const submit = form.onSubmit(async ({ code }) => {
    refusal.clear();
    try {
      const result = await confirm(digitsOf(code));
      onEnabled(result);
      setEnabled(result);
    } catch (error) {
      refusal.show(error);
      form.setFieldValue('code', '');
    }
  });

  if (enabled !== null) {
    return (
      <RecoveryCodes
        codes={enabled.recovery_codes}
        note={enabled.note}
        account={setup.account}
        onSaved={() => onDone(enabled)}
      />
    );
  }

  return (
    <form onSubmit={submit}>
      <Stack gap={FIELD_GAP}>
        <Text size="sm">
          Установите на телефон приложение-аутентификатор — Google Authenticator, Microsoft
          Authenticator, Aegis или менеджер паролей с кодами — и добавьте в него учётку.
        </Text>

        {/* Место QR-кода: та же ссылка `setup.uri` картинкой — отдельной правкой, это новая
            зависимость. Встанет здесь, над ссылкой и ключом. */}

        <Stack gap={6}>
          <Text size="sm" fw={500}>
            На телефоне
          </Text>
          <Button
            component="a"
            href={setup.uri}
            variant="light"
            className="press"
            style={{ alignSelf: 'flex-start' }}
          >
            Открыть в приложении
          </Button>
        </Stack>

        <Stack gap={6}>
          <Text size="sm" fw={500}>
            Или ключ вручную — {setup.issuer}, {setup.account}
          </Text>
          <Code
            block
            fz="md"
            className="glassQuiet"
            p="sm"
            aria-label="Ключ для приложения"
            style={{ letterSpacing: '0.06em', overflowWrap: 'anywhere', whiteSpace: 'normal' }}
          >
            {keyGroups(setup.secret)}
          </Code>
          <Group>
            <CopyButton value={setup.secret}>
              {({ copied, copy }) => (
                <Button size="compact-sm" variant="default" className="press" onClick={copy}>
                  {copied ? 'Скопировано' : 'Скопировать'}
                </Button>
              )}
            </CopyButton>
          </Group>
        </Stack>

        <CodeRefusalAlert refusal={refusal} title="Не включили" />

        <TextInput
          label="Код из приложения"
          description="Шесть цифр, которые приложение показывает для этой учётки"
          placeholder="123456"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={7}
          {...form.getInputProps('code')}
        />
        <Button
          type="submit"
          size="md"
          className="press"
          variant="gradient"
          loading={form.submitting}
        >
          Включить
        </Button>
      </Stack>
    </form>
  );
}
