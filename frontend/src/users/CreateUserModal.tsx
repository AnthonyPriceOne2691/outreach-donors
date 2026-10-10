/**
 * Заведение учётки: почта и роль, больше ничего.
 *
 * Пароль не спрашивается намеренно — его выдаёт система разовым.
 * Придуманный админом пароль он бы диктовал голосом, а сотрудник
 * оставлял бы навсегда.
 *
 * **Почта проверяется формой адреса, а не одним «@»** (проверка QA 10.10.2026):
 * `a@b` заводился учёткой, которую потом не удалить — только отключить, а отказ
 * «Почта — она же логин» не говорил, что не так. Правила и слова — те же, что
 * у сервера (`api/users/schemas.py`, `mail_problem`): здесь они экономят круг,
 * решает всё равно сервер.
 */

import { Alert, Button, Group, Modal, Select, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';

import type { OneTimePassword, Role } from '../api/types';
import { createUser } from '../api/users';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';

/** Ширина `users.email` на сервере. */
const MAIL_MAX = 255;
/** Домен почты: метки через точку, без пустых, зона — от двух знаков. */
const MAIL_DOMAIN = /^[^@\s.]+(?:\.[^@\s.]+)*\.[^@\s.]{2,}$/;

/** Что не так с почтой — словами сервера; `null` — годится. */
function mailProblem(raw: string): string | null {
  const email = raw.trim();
  if (email === '') return 'Впишите почту — она же логин';
  if (email.length > MAIL_MAX) return `Почта длиннее ${MAIL_MAX} знаков — таких адресов не бывает`;
  if (/\s/.test(email)) return 'В почте пробел — адрес пишется без пробелов';
  const [name = '', ...domains] = email.split('@');
  if (domains.length === 0) {
    return 'В почте нет «@» — нужен адрес целиком, например ivan@example.com';
  }
  if (domains.length > 1) return 'В почте больше одного «@» — в адресе он один';
  if (name === '') return 'Перед «@» нет имени ящика — например ivan@example.com';
  return MAIL_DOMAIN.test(domains[0] ?? '')
    ? null
    : 'После «@» нужен домен с зоной через точку — например example.com';
}

interface Props {
  opened: boolean;
  onClose: () => void;
  onCreated: (issued: OneTimePassword) => void;
}

export function CreateUserModal({ opened, onClose, onCreated }: Props) {
  const [refusal, setRefusal] = useState<string | null>(null);
  const form = useForm<{ email: string; role: Role }>({
    initialValues: { email: '', role: 'operator' },
    validate: {
      email: mailProblem,
    },
  });

  const submit = form.onSubmit(async ({ email, role }) => {
    setRefusal(null);
    try {
      const issued = await createUser(email, role);
      form.reset();
      onCreated(issued);
    } catch (error) {
      setRefusal(error instanceof Error ? error.message : 'Завести не удалось');
    }
  });

  return (
    <Modal opened={opened} onClose={onClose} title="Новая учётка">
      <form onSubmit={submit}>
        <Stack gap={FIELD_GAP}>
          {refusal !== null && (
            <Alert color="red" title="Не завели">
              {refusal}
            </Alert>
          )}
          <TextInput label="Почта" {...form.getInputProps('email')} />
          <Select
            label="Роль"
            allowDeselect={false}
            data={[
              // Права ролей — как на сервере (`access/permissions.py`, ROLE_PERMISSIONS).
              { value: 'operator', label: 'Оператор — база, прогоны, пороги, цены, продажи' },
              { value: 'admin', label: 'Админ — всё: ещё письма, домены рассылки, учётки' },
            ]}
            {...form.getInputProps('role')}
          />
          <Group justify="flex-end" mt={ACTIONS_GAP}>
            <Button variant="subtle" className="press" onClick={onClose}>
              Отмена
            </Button>
            <Button type="submit" className="press" variant="gradient" loading={form.submitting}>
              Завести
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
