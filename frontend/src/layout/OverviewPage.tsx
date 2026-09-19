/**
 * Обзор. Пока честно говорит, что уже в браузере, а что ещё командой.
 *
 * Пустая главная с надписью «добро пожаловать» хуже, чем список того,
 * чего здесь ещё нет: человек иначе ищет отсутствующий раздел глазами
 * и спрашивает, что сломалось.
 */

import { Badge, Card, Group, List, Stack, Text, Title } from '@mantine/core';

import { permissionTitle } from '../api/labels';
import { useSession } from '../auth/AuthProvider';

export function OverviewPage() {
  const { user } = useSession();

  return (
    <Stack maw={760} gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Обзор</Title>
          <Text>
            Вошли как <b>{user?.email}</b>.
          </Text>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              Доступные действия:
            </Text>
            {user?.permissions.map((permission) => (
              <Badge key={permission} variant="light">
                {permissionTitle(permission)}
              </Badge>
            ))}
          </Group>
        </Stack>
      </Card>

      <Card className="glass" p="lg">
        <Title order={5} mb="xs">
          Что сейчас в браузере
        </Title>
        <List size="sm" spacing={4}>
          <List.Item>Вход, смена своего пароля</List.Item>
          <List.Item>Учётки: завести, выдать права, сбросить пароль, отключить — админу</List.Item>
          <List.Item>Прогон: смета до запуска и запуск через очередь</List.Item>
          <List.Item>Доноры: таблица с фильтрами и карточка с адресами</List.Item>
          <List.Item>Диалоги: список переписок и карточка с распознанными ценами</List.Item>
          <List.Item>
            Домены рассылки: разгон, дневной расход, включение и выключение — админу
          </List.Item>
        </List>
      </Card>

      <Card className="glass" p="lg">
        <Title order={5} mb="xs">
          Что пока делается командой
        </Title>
        <List size="sm" spacing={4}>
          <List.Item>Сборка пула ключей по пресетам углов</List.Item>
          <List.Item>Поиск контактов ступенями</List.Item>
          <List.Item>Отправка писем: ждёт почтовых доменов и шаблона</List.Item>
        </List>
        <Text size="sm" c="dimmed" mt="sm">
          Экраны прогона и доноров — следующий срез. Порядок и причины — в проектном документе
          веб-слоя.
        </Text>
      </Card>
    </Stack>
  );
}
