/**
 * Заведение учётки: почта и роль, больше ничего.
 *
 * Пароль не спрашивается намеренно — его выдаёт система разовым.
 * Придуманный админом пароль он бы диктовал голосом, а сотрудник
 * оставлял бы навсегда.
 */

import { Alert, Button, Group, Modal, Select, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';

import type { OneTimePassword, Role } from '../api/types';
import { createUser } from '../api/users';

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
      email: (value) => (value.includes('@') ? null : 'Почта — она же логин'),
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
        <Stack>
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
              { value: 'operator', label: 'Оператор — база и прогоны' },
              { value: 'admin', label: 'Админ — ещё и учётки' },
            ]}
            {...form.getInputProps('role')}
          />
          <Group justify="flex-end">
            <Button variant="subtle" className="press" onClick={onClose}>
              Отмена
            </Button>
            <Button
              type="submit"
              className="press"
              variant="gradient"
              gradient={{ from: 'lagoon.5', to: 'lagoon.7', deg: 135 }}
              loading={form.submitting}
            >
              Завести
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
