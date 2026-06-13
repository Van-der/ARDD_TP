FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm install

# Copy application files
COPY . .

# Expose port 3000
EXPOSE 3000

# Start in dev mode to dynamically pick up VITE_ env vars passed by docker-compose at runtime.
# (If we built a static bundle here, it wouldn't be able to read runtime environment variables).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
