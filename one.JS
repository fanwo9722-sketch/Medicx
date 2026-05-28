import fs from 'fs';

const imageBuffer = fs.readFileSync('C:/Users/Austin/medical/istockphoto-173921449-170667a.jpg');
const base64Image = imageBuffer.toString('base64');

const response = await fetch('https://serverless.roboflow.com/iiiii-srapi/workflows/detect-count-and-visualize-2', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        api_key: 'op6ghK2hTwUAhTN83Ok0',
        inputs: {
            "image": {"type": "base64", "value": base64Image}
        }
    })
});
const result = await response.json();
console.log(JSON.stringify(result, null, 2));